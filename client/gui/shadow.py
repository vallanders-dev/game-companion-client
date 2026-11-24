"""client/gui/shadow.py - Ombri as a little shadow creature (2026-11-19).

The owner's direction: the name means shadow, so Ombri is one. Only a black head
with tufts of hair that sway slowly (owner, 2026-11-19; our own shape, not a trace
of the reference art), lit from behind by a neon rim that runs coral -> magenta ->
blue (owner's pick over coral only, 2026-11-19), with big pale eyes. No mouth, no floor line. The earlier ghost body
(`_silhouette`) is kept to come back to.

    idle         rests and bobs slowly, eyes dimmed, a blink every few seconds
    ouvindo      leans in, eyes wide, the rim brightens
    pensando     tilts back, kite-shaped eyes looking up to the side; the rim's
                 colours drift round it
    pesquisando  tilts the other way with a faint echo behind it (on the move)
    falando      half-moon eyes that open a little with the voice; the rim pulses
    pausado      eyes closed, the rim dim

Every change between states is eased (`_Motion`, ~0.3 s), and in the main
window the eyes follow the cursor (`look`). `calm=True` is the in-game look: just a dark disc with the neon rim (owner,
2026-11-19). Its brightness shows the state and it pulses while speaking; since 2026-11-23 (Tony) its gradient
circles the rim while listening, thinking or speaking. Nothing else moves while the player plays (owner, 2026-11-05).
client/gui/indicator.py picks between this, the eyes and the sphere.
"""
from __future__ import annotations

import math
import time

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


# Eye shapes, one per state, as numbers so one state can melt into the next
# (owner, 2026-11-19: smooth, animated changes between every state). Per eye:
# (x, y) shift in head units; oy a shift in eye units; w width; ht / hb the height
# above / below the eye's middle; k roundness (0.552 = an ellipse, 0 = straight
# sides, a diamond); a opacity. Order: (left eye, right eye).
_ROUND = 0.552
_EYE_SHAPES = {
    "idle":        ((0, 0, 0, .05, .05, .05, _ROUND, .7),) * 2,
    "ouvindo":     ((.025, 0, 0, .058, .06, .06, _ROUND, 1),) * 2,
    # Thinking: kite eyes looking up to the side (owner, 2026-11-19).
    "pensando":    ((-.03, -.03, 0, .076, .05, .076, 0, 1),) * 2,
    "pesquisando": ((.03, -.03, 0, .05, .05, .05, _ROUND, 1), (.03, -.03, 0, .05, .0275, .0275, _ROUND, 1)),
    # Half-moons, flat side down: the top lifts more with the voice (_TALK_LIFT).
    "falando":     ((0, 0, .018, .09, .034, .002, _ROUND, 1),) * 2,
    "pausado":     ((0, 0, 0, .08, .0175, .0175, _ROUND, .45),) * 2,
}
_TALK_LIFT = .016    # extra half-moon height at full voice, eye units
_BLINKS = {"idle": 1.0, "pensando": 1.0, "pesquisando": 1.0}
_GAZE = {"idle": 1.0, "ouvindo": 1.0, "falando": .7, "pensando": .25, "pesquisando": .25, "pausado": 0.0}
_SPIN = {"pensando": 40.0, "pesquisando": 40.0}      # rim colour drift, degrees a second
_ECHO = {"pesquisando": .35}
# In game (calm), the rim's gradient circles the disc while Ombri works (Tony,
# 2026-11-23): degrees a second. Idle and paused stay still.
_CALM_SPIN = {"ouvindo": 120.0, "pensando": 240.0, "pesquisando": 240.0, "falando": 90.0}
_HAIR = {"pausado": .3}
_BOB = {"idle": 1.0}
SETTLE = 0.09        # seconds: the time constant of every change (~0.3 s to settle)
_N_SCENE = 9         # numbers before the two eyes in _targets()


def _targets(state: str) -> list[float]:
    """The state's numbers: tilt, brightness, talking, hair sway, bob, rim spin,
    echo, how much the eyes follow the cursor, blinking, then both eyes."""
    tilt, bright = _POSE[state]
    flat = [tilt, bright, 1.0 if state == "falando" else 0.0, _HAIR.get(state, 1.0), _BOB.get(state, 0.0),
            _SPIN.get(state, 0.0), _ECHO.get(state, 0.0), _GAZE.get(state, 0.0), _BLINKS.get(state, 0.0)]
    for eye in _EYE_SHAPES[state]:
        flat.extend(float(v) for v in eye)
    return flat


class _Motion:
    """What Ombri shows right now, eased toward the state's numbers every frame,
    so a change of state (even mid-change) is a smooth move, never a jump."""

    def __init__(self) -> None:
        self.cur: list[float] | None = None
        self.last = 0.0
        self.angle = -40.0
        self.calm_angle = -40.0
        self.calm_speed = 0.0
        self.gaze = [0.0, 0.0]

    def step(self, state: str, look: tuple[float, float] | None, now: float) -> list[float]:
        goal = _targets(state)
        if self.cur is None:
            self.cur, self.last = goal, now
            return self.cur
        dt = max(0.0, min(0.25, now - self.last))
        self.last = now
        f = 1.0 - math.exp(-dt / SETTLE)
        self.cur = [c + (g - c) * f for c, g in zip(self.cur, goal)]
        gx, gy = look or (0.0, 0.0)
        fg = 1.0 - math.exp(-dt / 0.12)
        self.gaze = [self.gaze[0] + (gx - self.gaze[0]) * fg, self.gaze[1] + (gy - self.gaze[1]) * fg]
        self.angle = (self.angle + self.cur[5] * dt) % 360.0
        self.calm_speed += (_CALM_SPIN.get(state, 0.0) - self.calm_speed) * (1.0 - math.exp(-dt / 0.25))
        self.calm_angle = (self.calm_angle + self.calm_speed * dt) % 360.0
        return self.cur


_motions: dict[str, _Motion] = {}


def _motion(key: str) -> _Motion:
    if key not in _motions:
        _motions[key] = _Motion()
    return _motions[key]


def _eye_path(cx: float, cy: float, w: float, ht: float, hb: float, k: float) -> QPainterPath:
    """An eye from four points (left, top, right, bottom): k = 0.552 rounds it into
    an ellipse, 0 makes a diamond; hb = 0 flattens the bottom (a half-moon)."""
    hw = w / 2
    path = QPainterPath(QPointF(cx - hw, cy))
    path.cubicTo(QPointF(cx - hw, cy - ht * k), QPointF(cx - hw * k, cy - ht), QPointF(cx, cy - ht))
    path.cubicTo(QPointF(cx + hw * k, cy - ht), QPointF(cx + hw, cy - ht * k), QPointF(cx + hw, cy))
    path.cubicTo(QPointF(cx + hw, cy + hb * k), QPointF(cx + hw * k, cy + hb), QPointF(cx, cy + hb))
    path.cubicTo(QPointF(cx - hw * k, cy + hb), QPointF(cx - hw, cy + hb * k), QPointF(cx - hw, cy))
    path.closeSubpath()
    return path


def _eyes_from(painter: QPainter, s: float, m: list[float], gaze: tuple[float, float], t: float, level: float,
               big: float, ey: float) -> None:
    gap = 0.095 * s * (1 + (big - 1) * 0.5)
    se = s * big
    talk, gw, blink_w = m[2], m[7], m[8]
    blink = 1.0 - blink_w * (0.85 if (t % BLINK_EVERY) < BLINK_LEN else 0.0)
    gx, gy = gaze[0] * gw * 0.035 * s, gaze[1] * gw * 0.025 * s
    painter.setPen(Qt.PenStyle.NoPen)
    for i, side in enumerate((-1, 1)):
        x, y, oy, w, ht, hb, k, a = m[_N_SCENE + 8 * i: _N_SCENE + 8 * (i + 1)]
        ht = (ht + talk * _TALK_LIFT * level) * blink
        col = QColor(EYE)
        col.setAlphaF(max(0.0, min(1.0, a)))
        painter.setBrush(col)
        painter.drawPath(_eye_path(side * gap + x * s + gx, (ey + y) * s + oy * se + gy, w * se,
                                   max(ht * se, 0.004 * se), max(hb * blink * se, 0.0), k))


def _eyes(painter: QPainter, s: float, state: str, t: float, level: float, big: float = 1.0,
          ey: float = -0.14) -> None:
    """A state's eyes, still (icons and pictures); `big` scales them."""
    _eyes_from(painter, s, _targets(state), (0.0, 0.0), t, level, big, ey)


def draw_shadow(painter: QPainter, center: QPointF, box: float, state: str, t: float, *,
                level: float = 0.0, calm: bool = False, ring: QColor | None = None,
                look: tuple[float, float] | None = None) -> None:
    """Ombri in a `box` x `box` square at `center`. `level` (0..1) is the voice's
    loudness while speaking; `ring` outlines the box (in game: click-through cue);
    `look` (-1..1 each way) is where the cursor is, for the eyes to follow."""
    state = state if state in _POSE else "idle"
    s = box * 0.9
    level = max(0.0, min(1.0, level))
    mo = _motion("calm" if calm else "main")
    m = mo.step(state, look, time.monotonic())
    tilt, bright, talk, hair, bob_w, _spin, echo, gaze_w = m[:8]
    bright += talk * (0.4 * level - 0.15)   # speaking: 0.6 + 0.4 * level

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    cx, cy = center.x(), center.y()

    if ring is not None:
        painter.setPen(QPen(ring, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, box / 2 - 1, box / 2 - 1)

    if calm:
        # In game: only a dark disc with the neon rim. Its brightness says the
        # state; while speaking it pulses with the voice; while listening,
        # thinking or speaking the gradient circles the rim (_CALM_SPIN).
        painter.translate(cx, cy)
        disc = QPainterPath()
        disc.addEllipse(QPointF(0, 0), 0.36 * box, 0.36 * box)
        brush = _rim_brush(s, mo.calm_angle)
        _glow(painter, disc, s, brush, bright)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(BODY))
        painter.drawPath(disc)
        painter.setOpacity(max(0.0, min(1.0, 0.35 + 0.65 * bright)))
        painter.setPen(QPen(brush, max(1.5, 0.05 * box)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(disc)
        painter.restore()
        return

    bob = bob_w * 0.018 * s * math.sin(2 * math.pi * t / 3.2)
    tilt += mo.gaze[0] * gaze_w * 5.0

    painter.translate(cx, cy + bob)
    # Lean from the bottom of the head, so it tips rather than slides.
    painter.translate(0, (HEAD_CY + HEAD_RY) * s)
    painter.rotate(tilt)
    painter.translate(0, -(HEAD_CY + HEAD_RY) * s)

    path = _head(s, t, hair)
    brush = _rim_brush(s, mo.angle)

    if echo > 0.01:
        painter.save()
        painter.translate(-0.10 * s, 0.01 * s)
        _glow(painter, path, s, brush, echo)
        painter.restore()

    _glow(painter, path, s, brush, bright)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(BODY))
    painter.drawPath(path)
    rim = QPen(brush, max(1.0, 0.022 * s))
    painter.setOpacity(max(0.0, min(1.0, 0.35 + 0.65 * bright)))
    painter.setPen(rim)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.setOpacity(1.0)

    _eyes_from(painter, s, m, (mo.gaze[0], mo.gaze[1]), t, level, 1.3, EYE_Y)
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


def disc_image(size: int) -> QImage:
    """The in-game look as a still: a dark disc with the blue -> magenta -> coral
    rim. The tray icon and the favicons (owner, 2026-11-19)."""
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    rim_w = max(1.6, 0.09 * size)
    r = size / 2 - rim_w / 2 - (0.05 * size if size >= 32 else 0.3)
    p.translate(size / 2, size / 2)
    disc = QPainterPath()
    disc.addEllipse(QPointF(0, 0), r, r)
    brush = _rim_brush(size, -40.0)
    if size >= 32:
        _glow(p, disc, size * 0.5, brush, 0.8)
    p.setPen(QPen(brush, rim_w))
    p.setBrush(QColor(BODY))
    p.drawPath(disc)
    p.end()
    return img


def disc_icon() -> QIcon:
    """The tray icon, every size drawn on its own."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QPixmap.fromImage(disc_image(size)))
    return icon


def shadow_icon() -> QIcon:
    """Every tray size drawn on its own, so Windows never scales a big one down."""
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addPixmap(QPixmap.fromImage(shadow_image(size)))
    return icon
