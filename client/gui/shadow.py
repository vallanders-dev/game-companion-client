"""client/gui/shadow.py - Ombri as a little shadow creature (2026-11-19).

The owner's direction: the name means shadow, so Ombri is one. Only a black head
with tufts of hair that sway slowly (owner, 2026-11-19; our own shape, not a trace
of the reference art), lit from behind by a neon rim that runs coral -> magenta ->
blue (owner's pick over coral only, 2026-11-19), with big pale eyes. No mouth, no floor line. The earlier ghost body
(`_silhouette`) is kept to come back to.

    idle         rests and bobs slowly, eyes dimmed, a blink every few seconds
    ouvindo      leans in, eyes wide, the rim brightens
    pensando     tilts back, looks up to the side; in the main window the rim's
                 colours drift round it
    pesquisando  tilts the other way with a faint echo behind it (on the move)
    falando      half-moon eyes that open a little with the voice; the rim pulses
    pausado      eyes closed, the rim dim

`calm=True` is the in-game look: just a dark disc with the neon rim (owner,
2026-11-19). Its brightness shows the state and it pulses while speaking; nothing
else moves while the player plays (owner, 2026-11-05).
client/gui/indicator.py picks between this, the eyes and the sphere.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

CORAL = "#FF6B4A"
MAGENTA = "#E25DD0"
BLUE = "#5B7CFF"
BODY = "#06070A"
EYE = "#F4F0FF"

BLINK_EVERY = 4.6
BLINK_LEN = 0.14

# state -> (tilt in degrees, rim brightness 0..1)
_POSE = {
    "idle": (0.0, 0.55),
    "ouvindo": (7.0, 1.0),
    "pensando": (-8.0, 0.85),
    "pesquisando": (8.0, 0.85),
    "falando": (0.0, 0.75),
    "pausado": (0.0, 0.3),
}


def _silhouette(s: float, tail: float) -> QPainterPath:
    """The body around (0, 0) for a figure `s` tall: a round head over a short torso
    whose hem ends in three soft wisps, the middle one curling. `tail` (-1..1)
    swings that curl."""
    head = QPainterPath()
    head.addEllipse(QPointF(0, -0.15 * s), 0.28 * s, 0.28 * s)

    def pt(x: float, y: float) -> QPointF:
        return QPointF(x * s, y * s)

    curl = 0.03 * tail
    body = QPainterPath()
    body.moveTo(pt(-0.20, -0.05))
    body.cubicTo(pt(-0.16, 0.08), pt(-0.18, 0.22), pt(-0.15, 0.32))      # left flank
    body.cubicTo(pt(-0.14, 0.40), pt(-0.09, 0.41), pt(-0.07, 0.35))      # left wisp
    body.cubicTo(pt(-0.05, 0.43), pt(0.00 + curl, 0.46), pt(0.06 + curl, 0.44))  # middle wisp, curling
    body.cubicTo(pt(0.03, 0.42), pt(0.04, 0.38), pt(0.07, 0.35))
    body.cubicTo(pt(0.09, 0.41), pt(0.14, 0.40), pt(0.15, 0.32))         # right wisp
    body.cubicTo(pt(0.18, 0.22), pt(0.16, 0.08), pt(0.20, -0.05))        # right flank
    body.closeSubpath()
    return head.united(body)


# Hair tufts round the head: (angle on the head in degrees, Qt's y-down, length,
# base width in degrees, bend sideways, sway phase). Few wide tufts swept outwards
# from a parting just right of the top. Our own layout.
_TUFTS = (
    (176, 0.07, 30, -1.2, 0.0),
    (-148, 0.12, 38, -1.3, 0.9),
    (-112, 0.14, 36, -1.0, 1.8),
    (-80, 0.16, 32, 0.7, 2.7),
    (-48, 0.13, 38, 1.2, 3.6),
    (-16, 0.10, 34, 1.3, 4.5),
    (14, 0.06, 28, 1.2, 5.4),
)
HEAD_RX, HEAD_RY, HEAD_CY = 0.32, 0.28, 0.07
HAIR_PERIOD = 2.8   # seconds per sway
HAIR_SWAY = 7.0     # degrees at the tip
EYE_Y = 0.09        # eyes sit a little below the head's middle


def _head(s: float, t: float, sway: float = 1.0) -> QPainterPath:
    """Ombri's head for a figure `s` tall: a round head with tufts of hair whose
    tips sway slowly with time `t`. `sway` scales the motion (0 = still)."""
    rx, ry, cy = HEAD_RX * s, HEAD_RY * s, HEAD_CY * s

    def on_head(deg: float, inset: float = 1.0) -> QPointF:
        a = math.radians(deg)
        return QPointF(math.cos(a) * rx * inset, cy + math.sin(a) * ry * inset)

    shape = QPainterPath()
    shape.addEllipse(QPointF(0, cy), rx, ry)
    for deg, length, width, bend, phase in _TUFTS:
        b1, b2 = on_head(deg - width / 2, 0.9), on_head(deg + width / 2, 0.9)
        mid = (on_head(deg - width / 2) + on_head(deg + width / 2)) / 2
        out = math.radians(deg + sway * HAIR_SWAY * math.sin(2 * math.pi * t / HAIR_PERIOD + phase))
        d = QPointF(math.cos(out), math.sin(out))
        perp = QPointF(-d.y(), d.x())
        tip = mid + d * (length * s) + perp * (bend * length * s * 0.5)
        nudge = perp * (bend * length * s * 0.25)
        tuft = QPainterPath()
        tuft.moveTo(b1)
        tuft.quadTo(b1 + (tip - b1) * 0.55 + nudge, tip)
        tuft.quadTo(b2 + (tip - b2) * 0.55 + nudge, b2)
        tuft.closeSubpath()
        shape = shape.united(tuft)
    return shape


def _rim_brush(s: float, angle: float) -> QLinearGradient:
    a = math.radians(angle)
    dx, dy = math.cos(a) * 0.45 * s, math.sin(a) * 0.45 * s
    g = QLinearGradient(QPointF(-dx, -dy), QPointF(dx, dy))
    g.setColorAt(0.0, QColor(BLUE))
    g.setColorAt(0.5, QColor(MAGENTA))
    g.setColorAt(1.0, QColor(CORAL))
    return g


def _glow(painter: QPainter, path: QPainterPath, s: float, brush: QLinearGradient, bright: float) -> None:
    for width, alpha in ((0.13, 0.06), (0.085, 0.11), (0.05, 0.20)):
        painter.setOpacity(alpha * bright)
        pen = QPen(brush, max(1.2, width * s))
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(path)
    painter.setOpacity(1.0)


def _eyes(painter: QPainter, s: float, state: str, t: float, level: float, big: float = 1.0,
          ey: float = -0.14) -> None:
    """`big` scales the eyes (in game they'd be ~2 px otherwise)."""
    ey = ey * s
    gap = 0.095 * s * (1 + (big - 1) * 0.5)
    s_eye = s * big
    col = QColor(EYE)
    if state == "idle":
        col.setAlphaF(0.7)
    elif state == "pausado":
        col.setAlphaF(0.45)

    if state == "pausado":
        pen = QPen(col, max(1.0, 0.035 * s_eye))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for side in (-1, 1):
            painter.drawLine(QPointF(side * gap - 0.035 * s_eye, ey), QPointF(side * gap + 0.035 * s_eye, ey))
        return
    if state == "falando":
        # Half-moon eyes, flat side down; they open a little more with the voice.
        r = 0.045 * s_eye
        lift = r * (0.75 + 0.35 * max(0.0, min(1.0, level)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(col)
        for side in (-1, 1):
            half = QPainterPath()
            half.moveTo(side * gap - r, ey + r * 0.4)
            half.arcTo(QRectF(side * gap - r, ey + r * 0.4 - lift, 2 * r, 2 * lift), 180, -180)
            half.closeSubpath()
            painter.drawPath(half)
        return

    w, h, dx, dy, squint = 0.05 * s_eye, 0.10 * s_eye, 0.0, 0.0, 1.0
    if state == "ouvindo":
        w, h, dx = 0.058 * s_eye, 0.12 * s_eye, 0.025 * s
    elif state in ("pensando", "pesquisando"):
        side = -1 if state == "pensando" else 1
        dx, dy, squint = side * 0.03 * s, -0.03 * s, 0.55
    if state in ("idle", "pensando", "pesquisando") and (t % BLINK_EVERY) < BLINK_LEN:
        h = 0.015 * s_eye

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(col)
    squinting = -1 if state == "pensando" else 1
    for side in (-1, 1):
        eh = h * (squint if side == squinting else 1.0)
        painter.drawEllipse(QRectF(side * gap + dx - w / 2, ey + dy - eh / 2, w, eh))


def draw_shadow(painter: QPainter, center: QPointF, box: float, state: str, t: float, *,
                level: float = 0.0, calm: bool = False, ring: QColor | None = None) -> None:
    """Ombri in a `box` x `box` square at `center`. `level` (0..1) is the voice's
    loudness while speaking; `ring` outlines the box (in game: click-through cue)."""
    state = state if state in _POSE else "idle"
    tilt, bright = _POSE[state]
    s = box * 0.9
    level = max(0.0, min(1.0, level))

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    cx, cy = center.x(), center.y()

    if ring is not None:
        painter.setPen(QPen(ring, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, box / 2 - 1, box / 2 - 1)

    if calm:
        # In game: only a dark disc with the neon rim. Its brightness says the
        # state; while speaking it pulses with the voice. Nothing else moves.
        if state == "falando":
            bright = 0.6 + 0.4 * level
        painter.translate(cx, cy)
        disc = QPainterPath()
        disc.addEllipse(QPointF(0, 0), 0.36 * box, 0.36 * box)
        brush = _rim_brush(s, -40.0)
        _glow(painter, disc, s, brush, bright)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(BODY))
        painter.drawPath(disc)
        painter.setOpacity(0.35 + 0.65 * bright)
        painter.setPen(QPen(brush, max(1.5, 0.05 * box)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(disc)
        painter.restore()
        return

    bob = 0.0 if calm else (0.018 * s * math.sin(2 * math.pi * t / 3.2) if state == "idle" else 0.0)
    hair = 0.3 if state == "pausado" else 1.0
    if state == "falando":
        bright = 0.6 + 0.4 * level
    angle = -40.0 + (0.0 if calm or state not in ("pensando", "pesquisando") else (t * 40.0) % 360.0)

    painter.translate(cx, cy + bob)
    # Lean from the bottom of the head, so it tips rather than slides.
    painter.translate(0, (HEAD_CY + HEAD_RY) * s)
    painter.rotate(tilt)
    painter.translate(0, -(HEAD_CY + HEAD_RY) * s)

    path = _head(s, t, hair)
    brush = _rim_brush(s, angle)

    if state == "pesquisando" and not calm:
        painter.save()
        painter.translate(-0.10 * s, 0.01 * s)
        _glow(painter, path, s, brush, 0.35)
        painter.restore()

    _glow(painter, path, s, brush, bright)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(BODY))
    painter.drawPath(path)
    rim = QPen(brush, max(1.0, 0.022 * s))
    painter.setOpacity(0.35 + 0.65 * bright)
    painter.setPen(rim)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.setOpacity(1.0)

    _eyes(painter, s, state, t, level, big=1.3, ey=EYE_Y)
    painter.restore()


ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def shadow_image(size: int) -> QImage:
    """Ombri for the tray: the silhouette filling a `size` square, with a rim thick
    enough to outline the black body on a dark taskbar, big eyes, no floor line."""
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    rim_w = max(1.3, 0.05 * size)
    s = (size - 2 * rim_w) / 0.9
    p.translate(size / 2, size / 2 - 0.015 * s)
    path = _head(s, 0.0, 0.0)
    brush = _rim_brush(s, -40.0)
    if size >= 32:
        _glow(p, path, s, brush, 0.8)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(BODY))
    p.drawPath(path)
    p.setPen(QPen(brush, rim_w))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    _eyes(p, s, "ouvindo", 1.0, 0.0, big=2.0 if size <= 24 else 1.6, ey=EYE_Y)
    p.end()
    return img


def shadow_icon() -> QIcon:
    """Every tray size drawn on its own, so Windows never scales a big one down."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QPixmap.fromImage(shadow_image(size)))
    return icon
