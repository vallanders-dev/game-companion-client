"""client/gui/eyes.py - "Olhinhos": Ombri's state as two coral eyes (2026-11-18).

Chosen by the owner over the sphere (concept C in the 2026-11-18 session): small
enough to sit like a Discord avatar, one colour, and the pose tells the state
instead of four traffic-light colours.

    idle         eyes at rest, dimmed, a blink every few seconds
    ouvindo      eyes open wider and lift a little ("I hear you")
    pensando     eyes look up to the side, one squints; in the main window they drift
    pesquisando  same pose, glancing the other way (searching the web)
    falando      happy arcs ^ ^ bouncing with the voice
    pausado      closed: two flat lines

`calm=True` is the in-game look: fixed poses and the blink only, no drifting, so
nothing extra moves while the player plays (owner, 2026-11-05). The sphere stays in
client/gui/orb.py; client/gui/indicator.py picks between them.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from client.gui.orb import ACCENT

BLINK_EVERY = 4.6   # seconds between blinks
BLINK_LEN = 0.14    # seconds the lids stay shut
IDLE_ALPHA = 0.6


def _blinking(t: float) -> bool:
    return (t % BLINK_EVERY) < BLINK_LEN


def draw_eyes(painter: QPainter, center: QPointF, box: float, state: str, t: float, *,
              level: float = 0.0, calm: bool = False, backing: QColor | None = None,
              ring: QColor | None = None, color: str = ACCENT) -> None:
    """The eyes in a `box` x `box` square at `center`. `level` (0..1) is the voice's
    loudness while speaking. `backing` paints a dark rounded tile behind them (in
    game, so they read over any scene); `ring` outlines that tile (click-through cue)."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    cx, cy = center.x(), center.y()
    s = box

    if backing is not None:
        tile = QRectF(cx - s / 2, cy - s / 2, s, s)
        painter.setPen(QPen(ring, 1.0) if ring is not None else Qt.PenStyle.NoPen)
        painter.setBrush(backing)
        painter.drawRoundedRect(tile, s * 0.34, s * 0.34)
        s *= 0.78  # the eyes sit inside the tile

    col = QColor(color)
    if state == "idle":
        col.setAlphaF(IDLE_ALPHA)
    elif state == "pausado":
        col.setAlphaF(0.45)
    gap = 0.19 * s  # each eye's distance from the centre

    if state == "falando":
        bounce = -0.06 * s * max(0.0, min(1.0, level))
        pen = QPen(col, max(1.5, 0.085 * s))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        r = 0.115 * s
        for side in (-1, 1):
            ex, ey = cx + side * gap, cy + 0.05 * s + bounce
            painter.drawArc(QRectF(ex - r, ey - r, 2 * r, 2 * r), 20 * 16, 140 * 16)
        painter.restore()
        return

    if state == "pausado":
        pen = QPen(col, max(1.5, 0.07 * s))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        for side in (-1, 1):
            ex = cx + side * gap
            painter.drawLine(QPointF(ex - 0.07 * s, cy), QPointF(ex + 0.07 * s, cy))
        painter.restore()
        return

    w, h, dx, dy = 0.17 * s, 0.36 * s, 0.0, 0.0
    squint = 1.0
    if state == "ouvindo":
        pulse = 0.0 if calm else 0.03 * math.sin(2 * math.pi * t / 1.6)
        w, h, dy = 0.19 * s, (0.42 + pulse) * s, -0.03 * s
    elif state in ("pensando", "pesquisando"):
        side = 1 if state == "pensando" else -1
        drift = 0.0 if calm else 0.035 * math.sin(t * 2.2)
        dx, dy = side * (0.06 + drift) * s, -0.06 * s
        squint = 0.55
    if state in ("idle", "pensando", "pesquisando") and _blinking(t):
        h = 0.05 * s

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(col)
    for side in (-1, 1):
        eh = h * (squint if side == (1 if state != "pesquisando" else -1) else 1.0)
        rect = QRectF(cx + side * gap + dx - w / 2, cy + dy - eh / 2, w, eh)
        painter.drawRoundedRect(rect, min(w, eh) / 2, min(w, eh) / 2)
    painter.restore()
