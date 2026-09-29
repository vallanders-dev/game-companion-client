"""client/gui/orb.py — the Parça sphere, drawn with QPainter.

One drawing routine for every place the orb appears (the main window, the
in-game overlay, the tray and window icons), matching the approved desktop
mockup (2026-09-29): a glossy sphere lit from the upper left, the state
colour in its body, a soft glow around it and, in the main window, a thin
ring. It breathes slowly at rest and pulses faster while speaking.

The CSS in the mockup was `radial-gradient(circle at 35% 30%, #FFFFFFB3 0%,
<state> 34%, #15171D 80%)` plus `box-shadow: 0 0 <blur> <state>59`; the
numbers below are that gradient translated to Qt (a CSS "circle" gradient
at 35%/30% of the box reaches its farthest corner at ~1.91 radii).
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QRadialGradient

# State key -> colour. Keys are the client/server state names.
STATE_COLORS = {
    "idle": "#8A919E",
    "ouvindo": "#34D399",
    "pensando": "#F5B740",
    "pesquisando": "#F5B740",
    "falando": "#FF6B4A",
    "pausado": "#5A6170",
}
ACCENT = "#FF6B4A"
DARK_CORE = "#15171D"

# (seconds per cycle, scale amplitude): slow breath at rest, faster pulse
# while something is happening.
_MOTION = {
    "idle": (3.2, 0.045),
    "pausado": (5.0, 0.02),
    "ouvindo": (1.6, 0.06),
    "pensando": (2.4, 0.045),
    "pesquisando": (2.4, 0.045),
    "falando": (1.1, 0.07),
}


def state_color(state: str) -> QColor:
    return QColor(STATE_COLORS.get(state, STATE_COLORS["idle"]))


def breathing_scale(state: str, t: float) -> float:
    period, amount = _MOTION.get(state, _MOTION["idle"])
    return 1.0 + amount * math.sin(2 * math.pi * t / period)


def _alpha(color: QColor, a: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def draw_sphere(painter: QPainter, center: QPointF, radius: float, color: QColor, *,
                glow: float = 1.0, ring_radius: float | None = None) -> None:
    """The sphere at `center`. `glow` scales the glow's reach (1.0 = the
    mockup's: about one radius of soft light); `ring_radius` adds the main
    window's thin ring."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    cx, cy = center.x(), center.y()

    if glow > 0:
        reach = radius * (1.0 + 0.95 * glow)
        g = QRadialGradient(center, reach)
        g.setColorAt(0.0, _alpha(color, 0.35))
        g.setColorAt(radius / reach, _alpha(color, 0.30))
        g.setColorAt(1.0, _alpha(color, 0.0))
        painter.setBrush(g)
        painter.drawEllipse(center, reach, reach)

    focal = QPointF(cx - 0.30 * radius, cy - 0.40 * radius)
    body = QRadialGradient(focal, 1.91 * radius)
    body.setColorAt(0.0, QColor(255, 255, 255, 179))
    body.setColorAt(0.34, color)
    body.setColorAt(0.80, QColor(DARK_CORE))
    body.setColorAt(1.0, QColor(DARK_CORE))
    painter.setBrush(body)
    painter.drawEllipse(center, radius, radius)

    if ring_radius:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(_alpha(color, 0.25), 1.0))
        painter.drawEllipse(center, ring_radius, ring_radius)
    painter.restore()


def sphere_image(size: int, color: str = ACCENT, glow: float = 0.0) -> QImage:
    """A still sphere filling a square image (tray icon, window icon, .ico)."""
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    r = size / 2 / (1.0 + 0.95 * glow) - 0.5
    draw_sphere(p, QPointF(size / 2, size / 2), r, QColor(color), glow=glow)
    p.end()
    return img
