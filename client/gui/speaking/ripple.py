"""Ripple (owner, 2026-11-06; idea from their phone's charging animation): rings that follow
the voice's loudness and spread out from the orb's centre, bending the light of what is
under them like ripples on water, with a thin lit rim and fine coloured sparks. The orb
itself is drawn as always, into a picture, and this bends that picture.

Same look as the approved demo's shader (https://claude.ai/artifact/B1WPojrMZS7N1dCnR488WK),
in numpy because the window has no GPU surface. Nearly everything depends only on the
distance from the centre, so it's worked out once per distance in a table and only the
pixels a ripple touches get computed. With no ripple alive, this just draws the orb.
"""
from __future__ import annotations

import math
import random

import numpy as np
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from client.gui.orb import state_color

BACKGROUND = "#111318"   # the main window's; the picture is opaque, so it has to match

MAX_RIPPLES = 8
START, SPEED, LIFE = 0.05, 0.62, 1.5   # in half-widths of the widget, and seconds
BEND = 1.0
MAX_DPR = 1.5   # finer costs far more (eval/speaking_designs_harness.py) and these rings are soft

# Loudness -> a new ripple: a rise over the recent average, at most one per MIN_GAP.
LOUDNESS_GAIN, SPAWN_FLOOR, RISE, MIN_GAP = 4.5, 0.1, 1.2, 0.18
AVERAGE = 0.26   # per 100 ms slice: about the demo's 0.3 s average
# Audio reaches the device's buffer before it's heard (~180 ms, see speech.AudioOut),
# so a ripple starts that much after its slice arrives.
VOICE_LEAD = 0.18

_LUT_RES = 512   # table steps per half-width
_LUT_DIST = np.arange(int(math.sqrt(2) * _LUT_RES) + 2, dtype=np.float32) / _LUT_RES
_CELLS = 240     # spark positions around a ring
_BGR_BEND = (0.59, 0.5, 0.42)   # blue bends most, as light does


def _smoothstep(e0: float, e1: float, x):
    u = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return u * u * (3 - 2 * u)


_LUT_EDGE = _smoothstep(1.0, 0.82, _LUT_DIST)   # everything fades before the widget's edge


def _hash(n):
    x = np.sin(n * 12.9898) * 43758.5453
    return x - np.floor(x)


class _Grid:
    """What each pixel of an n x n picture needs, flattened, y down."""

    def __init__(self, n: int) -> None:
        self.n = n
        ys, xs = np.mgrid[0:n, 0:n].astype(np.float32)
        px = (xs + 0.5) / n * 2 - 1
        py = (ys + 0.5) / n * 2 - 1
        d = np.maximum(np.hypot(px, py), 1e-4)
        self.xs, self.ys = xs.ravel(), ys.ravel()
        self.dist = np.minimum((d * _LUT_RES).astype(np.int32), len(_LUT_DIST) - 1).ravel()
        self.dir_x, self.dir_y = (px / d).ravel(), (py / d).ravel()
        # The orb is lit from the upper left.
        self.light = np.clip(0.25 + 0.75 * np.maximum(self.dir_x * -0.6 - self.dir_y * 0.8, 0.0), 0.0, 1.0)
        around = (np.arctan2(-py, px) + math.pi) / (2 * math.pi) * _CELLS
        self.cell = np.minimum(around.astype(np.int32), _CELLS - 1).ravel()
        self.window = _smoothstep(0.42, 0.12, np.abs(around - np.floor(around) - 0.5)).ravel()


class _Ripple:
    __slots__ = ("t0", "strength", "lit", "offset", "colour")

    def __init__(self, t0: float, strength: float) -> None:
        self.t0, self.strength = t0, min(1.0, strength)
        seed = random.random() * 10
        cells = np.arange(_CELLS, dtype=np.float64)
        self.lit = _hash(cells + seed * 91.0) > 0.86
        self.offset = ((_hash(cells + seed * 17.0) - 0.5) * 3.0).astype(np.float32)
        hue = _hash(cells * 0.37 + seed)[:, None]
        # Blue, green, red: the picture's byte order.
        self.colour = (0.62 + 0.38 * np.cos(6.2831 * (hue + np.array([0.67, 0.33, 0.0])))).astype(np.float32)


class RippleDesign:
    def __init__(self) -> None:
        self._ripples: list[_Ripple] = []
        self._average = 0.0
        self._last_spawn = -1e9
        self._grid: _Grid | None = None

    def feed_level(self, level: float, t: float) -> None:
        raw = min(1.0, level * LOUDNESS_GAIN)
        self._average += (raw - self._average) * AVERAGE
        if raw > SPAWN_FLOOR and raw > self._average * RISE and t - self._last_spawn > MIN_GAP:
            self._spawn(0.4 + raw * 0.8, t + VOICE_LEAD)
            self._last_spawn = t

    def _spawn(self, strength: float, t0: float) -> None:
        if len(self._ripples) >= MAX_RIPPLES:
            self._ripples.pop(0)
        self._ripples.append(_Ripple(t0, strength))

    def paint(self, p: QPainter, center: QPointF, radius: float | None, t: float, state: str, draw_orb,
              box: int, dpr: float) -> None:
        self._ripples = [r for r in self._ripples if t - r.t0 < LIFE]
        alive = [r for r in self._ripples if t >= r.t0]
        if not alive:
            draw_orb(p)
            return
        n = int(round(box * min(dpr, MAX_DPR)))
        img = QImage(n, n, QImage.Format.Format_RGB32)
        img.setDevicePixelRatio(n / box)
        img.fill(QColor(BACKGROUND))
        q = QPainter(img)
        draw_orb(q)
        q.end()
        self._bend(img, n, alive, t, state_color(state))
        p.drawImage(QRectF(0, 0, box, box), img)

    def _bend(self, img: QImage, n: int, alive: list[_Ripple], t: float, tint: QColor) -> None:
        if self._grid is None or self._grid.n != n:
            self._grid = _Grid(n)
        g = self._grid
        pixels = np.frombuffer(img.bits(), np.uint8).reshape(n, n, 4)
        src = pixels.copy()

        # Everything that depends only on the distance from the centre, per ripple.
        rings = []
        bend = np.zeros_like(_LUT_DIST)
        rim = np.zeros_like(_LUT_DIST)
        shade = np.zeros_like(_LUT_DIST)
        for r in alive:
            age = t - r.t0
            k = age / LIFE
            a = r.strength * (1 - k * k * (3 - 2 * k)) * min(1.0, age * 5)
            if a < 0.08 and k > 0.5:   # nearly faded: can't be seen, and worth the time saved
                continue
            x = (_LUT_DIST - (START + SPEED * age)) / (0.05 + 0.05 * age)
            near = np.abs(x) <= 5.0
            bend += np.where(near, a * x * np.exp(-x * x), 0.0)
            rim += np.where(near, a * np.exp(-x * x * 10.0), 0.0)
            shade += np.where(near, a * np.exp(-(x + 1.3) ** 2 * 1.6), 0.0)
            rings.append((r, a, x, np.abs(x) < 3.2))
        bend *= _LUT_EDGE * 0.05 * BEND
        darken = np.minimum(shade * 0.25, 0.4) * _LUT_EDGE
        rim *= 0.4 * _LUT_EDGE

        # Only the pixels a ripple visibly reaches (a tenth of a pixel of bend, one colour level).
        touched = np.flatnonzero(((np.abs(bend) * n > 0.1) | (darken > 4e-3) | (rim > 4e-3))[g.dist])
        if touched.size == 0:
            return
        dist = g.dist[touched]
        shift = bend[dist] * n
        keep = 1.0 - darken[dist]
        lit = rim[dist] * g.light[touched]
        sx, sy = g.xs[touched], g.ys[touched]
        dx, dy = g.dir_x[touched] * shift, g.dir_y[touched] * shift

        source = src.reshape(-1, 4)
        out = np.empty((touched.size, 3), np.float32)
        for ch, amount in enumerate(_BGR_BEND):   # the light splits a little: each colour bends its own amount
            x = np.clip(sx - dx * amount, 0, n - 1.001)
            y = np.clip(sy - dy * amount, 0, n - 1.001)
            x0, y0 = x.astype(np.int32), y.astype(np.int32)
            fx, fy = x - x0, y - y0
            at = y0 * n + x0
            layer = source[:, ch]
            out[:, ch] = ((layer.take(at) * (1 - fx) + layer.take(at + 1) * fx) * (1 - fy)
                          + (layer.take(at + n) * (1 - fx) + layer.take(at + n + 1) * fx) * fy) * keep

        for ch, value in enumerate((tint.blueF(), tint.greenF(), tint.redF())):
            out[:, ch] += min(1.0, value + 0.1) * lit * 255.0

        # The sparks: fine coloured specks strewn along each ring.
        cell = g.cell[touched]
        window = g.window[touched]
        sparkable = window > 0
        edge = _LUT_EDGE[dist]
        for r, a, x, band in rings:
            hit = np.flatnonzero(r.lit[cell] & band[dist] & sparkable)
            if hit.size:
                c = cell[hit]
                off = x[dist[hit]] - r.offset[c]
                glow = a * np.exp(-off * off * 7.0) * window[hit] * edge[hit] * 0.6 * 255.0
                out[hit] += r.colour[c] * glow[:, None]

        flat = pixels.reshape(-1, 4)
        flat[touched, :3] = np.clip(out, 0, 255).astype(np.uint8)
