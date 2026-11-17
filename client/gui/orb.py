"""client/gui/orb.py — the Ombri sphere, drawn with QPainter.

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
import sys

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QConicalGradient, QImage, QPainter, QPainterPath, QPen, QRadialGradient

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


HALO_STATES = ("pensando", "pesquisando")
_HALO_STRANDS = 8
_HALO_SPECKS = 64


def _shift_hue(color: QColor, degrees: float) -> QColor:
    h, s, v, _a = color.getHsvF()
    return QColor.fromHsvF(((h if h >= 0 else 0.0) + degrees / 360.0) % 1.0, s, v)


def _ring_path(cx: float, cy: float, radius: float, t: float, phase: float, steps: int = 64) -> QPainterPath:
    path = QPainterPath()
    for i in range(steps + 1):
        a = 2 * math.pi * i / steps
        # Two slow waves travelling round the ring in opposite directions.
        r = radius * (1.0 + 0.018 * math.sin(3 * a + 1.5 * t + phase) + 0.012 * math.sin(5 * a - 2.1 * t + 1.7 * phase))
        pt = QPointF(cx + r * math.cos(a), cy + r * math.sin(a))
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    return path


def draw_thinking_halo(painter: QPainter, center: QPointF, radius: float, color: QColor, t: float,
                       strength: float = 1.0) -> None:
    """The ring of light around the orb while Ombri thinks (owner's idea,
    2026-11-04): a soft glow band, a bundle of fine wavy strands whose
    colour drifts round the ring a little either side of the state colour
    (so the edge shimmers like oil on water), and dust drifting through it.
    `radius` is the ring's; `strength` 0..1 fades it in and out. Everything
    is a function of `t` (no randomness), so a frame redraws identically
    and the cost is fixed (9 paths of 64 points + 64 dots, ~2 ms at the main
    window's size, only while thinking)."""
    if strength <= 0.01:
        return
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    cx, cy = center.x(), center.y()

    glow = QPen(_alpha(color, 0.10 * strength), radius * 0.16)
    painter.setPen(glow)
    painter.drawPath(_ring_path(cx, cy, radius, t, 0.0))

    # The iridescent colour: hue a little off the state colour, rotating slowly.
    grad = QConicalGradient(center, (t * 25.0) % 360.0)
    for k, deg in enumerate((0, -40, 25, 50, -20, 0)):
        grad.setColorAt(k / 5, _alpha(_shift_hue(color, deg), 1.0))
    for j in range(_HALO_STRANDS):
        spread = (j - (_HALO_STRANDS - 1) / 2) / _HALO_STRANDS  # -0.5..0.5 across the band
        r = radius * (1.0 + 0.07 * spread)
        alpha = (0.22 + 0.25 * (1 - abs(spread) * 2)) * strength
        pen = QPen(QBrush(grad), 0.9)
        painter.setOpacity(max(0.0, min(1.0, alpha)))
        painter.setPen(pen)
        painter.drawPath(_ring_path(cx, cy, r, t * (0.85 + 0.03 * j), j * 0.9))
    painter.setOpacity(1.0)

    painter.setPen(Qt.PenStyle.NoPen)
    for i in range(_HALO_SPECKS):
        base = i * 2.39996  # golden angle: evenly spread, never in rows
        a = base + t * (0.18 + 0.12 * ((i * 7) % 5) / 4) * (1 if i % 2 else -1)
        r = radius * (1.0 + 0.09 * math.sin(i * 1.7 + t * 0.8))
        twinkle = 0.5 + 0.5 * math.sin(t * (1.8 + (i % 3) * 0.7) + i)
        size = 0.45 + 0.9 * twinkle
        painter.setBrush(_alpha(_shift_hue(color, (i % 5 - 2) * 18.0) if i % 4 else QColor(255, 255, 255),
                                0.8 * twinkle * strength))
        painter.drawEllipse(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)), size, size)
    painter.restore()


SPEAK_STATES = ("falando",)

def sphere_image(size: int, color: str = ACCENT, glow: float = 0.0) -> QImage:
    """A still sphere filling a square image (tray icon, window icon, .ico)."""
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    r = size / 2 / (1.0 + 0.95 * glow) - 0.5
    draw_sphere(p, QPointF(size / 2, size / 2), r, QColor(color), glow=glow)
    p.end()
    return img


def claim_taskbar_identity() -> None:
    """Call before any window exists. Ombri runs under pythonw.exe, so
    Windows would group its windows under Python in the taskbar and show
    Python's icon (seen on the first real install, 2026-09-29). Its own
    AppUserModelID makes the taskbar use Ombri's window icon instead."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Parca.Desktop")
    except (OSError, AttributeError):
        pass
    # Notifications are headed by the ID's registered name - with none, Windows
    # printed "Parca.Desktop" over "Ombri is still here in the tray" (owner's
    # recording, 2026-10-29).
    try:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\AppUserModelId\Parca.Desktop") as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "Ombri")
    except OSError:
        pass
