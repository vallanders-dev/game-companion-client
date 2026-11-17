"""Atom (owner, 2026-11-04; in the app until the ripple replaced it, 2026-11-06): six balls
on tilted ellipses around the orb, like an atom drawing, each ellipse changing shape from
lap to lap. It ignores the voice's loudness and eases in and out over ~0.3 s with the
talking state. A function of `t`, with a fixed cost (6 arcs of 48 points + 48 dots), only
while talking."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

from client.gui.orb import SPEAK_STATES, _alpha, state_color

_ORBIT_BALLS = 6
_ORBIT_REACH = 1.12   # the longest axis, as a share of the given radius (stays inside the widget)
_ORBIT_STEPS = 48
_ORBIT_TRAIL = 7


def _orbit_shape(ball: int, lap: int) -> float:
    """This lap's squash, 0.40..0.85 of the long axis: a fixed pseudo-random value
    per ball and lap, so each lap has its own shape and a frame always redraws the same."""
    x = math.sin(lap * 12.9898 + ball * 78.233) * 43758.5453
    return 0.40 + 0.45 * (x - math.floor(x))


def _orbit_point(cx: float, cy: float, a: float, b: float, angle: float, tilt: float) -> QPointF:
    x, y = a * math.cos(angle), b * math.sin(angle)
    return QPointF(cx + x * math.cos(tilt) - y * math.sin(tilt), cy + x * math.sin(tilt) + y * math.cos(tilt))


def draw_speaking_orbits(painter: QPainter, center: QPointF, radius: float, color: QColor, t: float,
                         strength: float, front: bool) -> None:
    """Called twice per frame, `front=False` before the sphere and `front=True` after
    it: a ball on the far half of its orbit passes behind the sphere, which is drawn
    exactly as always."""
    if strength <= 0.01:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    cx, cy = center.x(), center.y()
    light = QColor(255, 236, 228)
    for ball in range(_ORBIT_BALLS):
        tilt = ball * math.pi / _ORBIT_BALLS
        a = radius * _ORBIT_REACH * (0.90 + 0.10 * ((ball * 2) % 3) / 2) * (0.82 + 0.18 * strength)
        turns = t / (2.3 + 0.28 * ((ball * 5) % _ORBIT_BALLS)) + ball * 0.37
        lap = math.floor(turns)
        u = turns - lap
        blend = u * u * (3 - 2 * u)  # the shape eases into the next lap's, with no jump
        b = a * (_orbit_shape(ball, lap) * (1 - blend) + _orbit_shape(ball, lap + 1) * blend)
        angle = 2 * math.pi * u

        # The orbit line: the far half before the sphere, the near half after.
        path = QPainterPath()
        for i in range(_ORBIT_STEPS // 2 + 1):
            step = 2 * math.pi * i / _ORBIT_STEPS + (0.0 if front else math.pi)
            pt = _orbit_point(cx, cy, a, b, step, tilt)
            if i == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(_alpha(color, 0.20 * strength), 0.8))
        painter.drawPath(path)

        # The ball and its short tail, each dot on the side of the sphere it's on.
        painter.setPen(Qt.PenStyle.NoPen)
        for k in range(_ORBIT_TRAIL, -1, -1):
            at = angle - k * 0.085
            if (math.sin(at) >= 0) != front:
                continue
            near = 0.5 + 0.5 * math.sin(at)  # 1 closest to the viewer
            size = (1.9 + 1.7 * near) * (1 - k / (_ORBIT_TRAIL + 1)) ** 0.6 * max(0.3, strength)
            shade = light if k == 0 else color
            painter.setBrush(_alpha(shade, (1.0 if k == 0 else 0.42 * (1 - k / (_ORBIT_TRAIL + 1))) * strength))
            painter.drawEllipse(_orbit_point(cx, cy, a, b, at, tilt), size, size)
    painter.restore()


class AtomDesign:
    def __init__(self) -> None:
        self._voice = 0.0

    def feed_level(self, level: float, t: float) -> None:
        pass

    def paint(self, p: QPainter, center: QPointF, radius: float | None, t: float, state: str, draw_orb,
              box: int, dpr: float) -> None:
        if not radius:
            draw_orb(p)
            return
        self._voice += ((1.0 if state in SPEAK_STATES else 0.0) - self._voice) * 0.12
        color = state_color("falando")
        draw_speaking_orbits(p, center, radius, color, t, self._voice, front=False)
        draw_orb(p)
        draw_speaking_orbits(p, center, radius, color, t, self._voice, front=True)
