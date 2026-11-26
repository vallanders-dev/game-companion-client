"""client/gui/build_pane.py — the Build pane: Ombri's orb becomes a hand-made
Minecraft build shown one layer at a time (server/build_guide.py; the owner's
idea and Claude's brief, docs/agents/arena_handoff.md, 2026-11-22; PR 2 of 4).

What is here
- `GuideMorph`: an overlay on the window's Ombri view. Closed, it is hidden
  and the status page is as always. Opening a guide is NOT a page switch:
  the orb (the shadow head's dark body with its neon rim) swells, its edge
  goes liquid, and that one shape stretches into the pane's rounded glass
  rectangle - `blob_path()` interpolates the outline point by point from the
  head's ellipse to the rounded rect, with low-frequency wobbles that are
  zero at both ends - and the content fades in only once the shape has
  settled. Closing is the reverse, ending with one overshoot-and-settle
  breath before the real orb is shown again. ~0.7 s open, ~0.66 s close,
  ease-out, 30 fps at most; the timer runs only while something moves and
  never while the window is hidden. Reduced motion (Windows' "show
  animations" off, or OMBRI_REDUCED_MOTION=1) = a plain fade, no wobble.
- `BuildPane`: the content. A small orb stays alive in its corner (the same
  OrbWidget as the status page, so it listens, thinks and speaks as always);
  a voice command sends a ripple from it across the pane (`_RippleLayer`).
  The layer "4 / 9" large; the layer grid (`LayerGrid`: rows back to front,
  the front row at the bottom; this layer's blocks lit, air over blocks of
  the layer below as a dim footprint; a layer change ripples out from the
  grid's centre, ~340 ms; a faint slow shimmer on the glass); the hint; the
  material chips (colour, name, this layer's count, what is left of the
  total - all counted by the server's code, never typed here); round glass
  back / next buttons; the keys. The list page ("quero construir uma casa",
  or the status page's "Construir" button): the builds as rows to click.
  With listening mode on, "próxima" / "volta" said alone are heard on the PC
  (client/voice_commands.py GUIDE_PHRASES) and arrive as a `step` message:
  the pane moves as for its keys, with the spoken command's ripple.

The state lives here, in memory: the open build (the whole JSON the server
sent in the `guide` message - client/ is public, the builds are not), the
layer, whether the list is showing. Every change is reported upward
(`state_changed`) so main.py's next `turn_start.guide` says where the pane
is. Nothing is written anywhere.

2026-11-24 (the in-game pane, client/gui/game_pane.py): the morph's timing
is the module functions `morph_progress()`, `content_opacity()` and
`face_opacity()`, shared by `GuideMorph` here and the pane over the game, so
both move exactly alike.

2026-11-24 (PR 3b, the real screenshots): every step shows the owner's own
game screenshot of the build up to that layer (`_Shot`, above the grid), and
the chips carry the block's icon (crops of the owner's inventory). Both come
from the server only (server/build_shots.py): the `guide` message brings the
step's `shot` when a voice command moves the pane; main.py fetches the rest
after an `open` and sends them as `guide_assets` messages; `ASSETS` keeps
them here for the run, shared with the in-game pane. A step without its
picture (a new build before its shots exist, an old server) shows the grid
alone, as before; a block without its icon shows its colour square. When
the last layer is done, `finished` carries the build so the in-game pane
can show the finished house (its front shot).
"""
from __future__ import annotations

import math
import os
import sys
import time

import base64

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QToolTip, QVBoxLayout, QWidget,
)

from client.build_assets import block_id, valid_shot
from client.gui import indicator
from client.gui.fonts import HEADING_FAMILY
from client.gui.shadow import BLUE, BODY, CORAL, HEAD_CY, HEAD_RX, MAGENTA
from client.gui.ui_texts import ui
from client.gui.widgets import ACCENT, TEXT, TEXT_2, TEXT_3, _label, line_icon

# Timing (ms). The orb's own family: the shadow's state changes settle in ~0.3 s.
OPEN_MS = 700        # the drop swells 0-90, stretches 90-560, the content fades in 520-700
CLOSE_MS = 660       # content fades out 0-140, shape pulls back 120-540, one breath to 660
FACE_MS = 160        # Ombri's face melts into the drop as it forms (and comes back as it settles)
FADE_MS = 240        # reduced motion: the plain fade, both ways
LAYER_MS = 340       # a layer change ripples out from the grid's centre
RIPPLE_MS = 760      # a voice command's ring from the small orb across the pane
FRAME_MS = 33        # ~30 fps, the orb's own rate
SHIMMER_MS = 66      # the grid's caustic shimmer, ~15 fps: faint and slow, so it stays cheap

PANE_MARGIN = 12
PANE_RADIUS = 24
GLASS = "#141821"          # the pane's body (the head is BODY, near black: the fill moves between the two)
CELL_MAX = 28
_OUTLINE_POINTS = 160


def reduced_motion() -> bool:
    """Windows' "Show animations" off (SPI_GETCLIENTAREAANIMATION), or
    OMBRI_REDUCED_MOTION=1 for a test: every move becomes a plain fade."""
    if os.environ.get("OMBRI_REDUCED_MOTION", "").strip() in ("1", "true", "yes"):
        return True
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        flag = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(flag), 0)  # SPI_GETCLIENTAREAANIMATION
        return bool(ok) and not flag.value
    except Exception:  # noqa: BLE001 - no user32, odd Windows: assume animations are fine
        return False


# --------------------------------------------------------------------------- #
# Shape maths
# --------------------------------------------------------------------------- #
def _clamp(u: float) -> float:
    return 0.0 if u < 0.0 else 1.0 if u > 1.0 else u


def _ease_out(u: float) -> float:
    u = _clamp(u)
    return 1.0 - (1.0 - u) ** 3


def _smooth(u: float) -> float:
    u = _clamp(u)
    return u * u * (3.0 - 2.0 * u)


def _rounded_rect_points(rect: QRectF, radius: float, n: int) -> list[QPointF]:
    """`n` points along a rounded rectangle's outline, evenly spaced by arc
    length, clockwise on screen from the top-left corner's end."""
    r = max(0.0, min(radius, rect.width() / 2, rect.height() / 2))
    w, h = rect.width() - 2 * r, rect.height() - 2 * r
    arc = math.pi * r / 2
    x0, y0, x1, y1 = rect.left(), rect.top(), rect.right(), rect.bottom()
    # (kind, length, data): straight edges and quarter arcs, clockwise.
    segments = (
        ("line", w, (QPointF(x0 + r, y0), QPointF(1, 0))),
        ("arc", arc, (QPointF(x1 - r, y0 + r), -90.0)),
        ("line", h, (QPointF(x1, y0 + r), QPointF(0, 1))),
        ("arc", arc, (QPointF(x1 - r, y1 - r), 0.0)),
        ("line", w, (QPointF(x1 - r, y1), QPointF(-1, 0))),
        ("arc", arc, (QPointF(x0 + r, y1 - r), 90.0)),
        ("line", h, (QPointF(x0, y1 - r), QPointF(0, -1))),
        ("arc", arc, (QPointF(x0 + r, y0 + r), 180.0)),
    )
    total = 2 * w + 2 * h + 4 * arc
    points: list[QPointF] = []
    seg = 0
    seg_start = 0.0
    for i in range(n):
        d = total * i / n
        while seg < len(segments) - 1 and d > seg_start + segments[seg][1]:
            seg_start += segments[seg][1]
            seg += 1
        kind, length, data = segments[seg]
        local = d - seg_start
        if kind == "line":
            start, direction = data
            points.append(QPointF(start.x() + direction.x() * local, start.y() + direction.y() * local))
        else:
            centre, a0 = data
            a = math.radians(a0 + 90.0 * (local / length if length else 0.0))
            points.append(QPointF(centre.x() + r * math.cos(a), centre.y() + r * math.sin(a)))
    return points


def blob_path(orb_centre: QPointF, rx: float, ry: float, pane: QRectF, radius: float, e: float,
              wobble: float, t: float, swell: float = 1.0) -> QPainterPath:
    """The one shape between the orb's ellipse (`e` = 0) and the pane's rounded
    rectangle (`e` = 1). Each outline point of the rectangle is paired with the
    ellipse point at the same angle from the pane's centre and moved between
    the two; `swell` scales the ellipse (the drop fills out before it
    stretches); `wobble` (0..1, zero at both ends of a move) adds a few
    low-frequency waves along the outline - the drop's liquid edge."""
    if e >= 1.0 and wobble <= 0.0:
        path = QPainterPath()
        path.addRoundedRect(pane, radius, radius)
        return path
    rect_pts = _rounded_rect_points(pane, radius, _OUTLINE_POINTS)
    pc = pane.center()
    diag = math.hypot(pane.width(), pane.height())
    size = 2 * min(rx, ry) * swell + (diag - 2 * min(rx, ry) * swell) * e  # how big the shape is now
    amp = 0.03 * size * wobble
    path = QPainterPath()
    for i, rp in enumerate(rect_pts):
        phi = math.atan2(rp.y() - pc.y(), rp.x() - pc.x())
        ox = orb_centre.x() + swell * rx * math.cos(phi)
        oy = orb_centre.y() + swell * ry * math.sin(phi)
        x = ox + (rp.x() - ox) * e
        y = oy + (rp.y() - oy) * e
        if amp > 0.0:
            wave = math.sin(3 * phi + 6.0 * t) + 0.6 * math.sin(5 * phi - 9.0 * t + 1.7) + 0.35 * math.sin(2 * phi + 4.0 * t)
            cx = pc.x() + (orb_centre.x() - pc.x()) * (1.0 - e)
            cy = pc.y() + (orb_centre.y() - pc.y()) * (1.0 - e)
            dx, dy = x - cx, y - cy
            dist = math.hypot(dx, dy) or 1.0
            x += dx / dist * amp * wave
            y += dy / dist * amp * wave
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.closeSubpath()
    return path


def _rim(rect: QRectF) -> QLinearGradient:
    """The shadow head's neon rim (blue -> magenta -> coral) across a shape."""
    g = QLinearGradient(rect.topLeft(), rect.bottomRight())
    g.setColorAt(0.0, QColor(BLUE))
    g.setColorAt(0.5, QColor(MAGENTA))
    g.setColorAt(1.0, QColor(CORAL))
    return g


def _mix(a: str, b: str, u: float) -> QColor:
    ca, cb = QColor(a), QColor(b)
    u = _clamp(u)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * u), round(ca.green() + (cb.green() - ca.green()) * u),
                  round(ca.blue() + (cb.blue() - ca.blue()) * u))


def _alpha(color: QColor | str, a: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(_clamp(a))
    return c


def paint_glass(p: QPainter, path: QPainterPath, bright: float, fill: QColor, opacity: float = 1.0,
                s: float = 110.0) -> None:
    """A shape in Ombri's own language: the neon glow outside (the shadow
    head's three soft strokes, `shadow._glow`'s widths), a dark body, a glass
    highlight and a soft inner glow at the edges, a thin lit rim. `s` is the
    glow's scale (the head's own, so the pane's halo matches the orb's);
    `opacity` fades the whole (reduced motion)."""
    rect = path.boundingRect()
    brush = _rim(rect)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for width, alpha in ((0.13, 0.06), (0.085, 0.11), (0.05, 0.20)):
        p.setOpacity(_clamp(alpha * bright * opacity))
        pen = QPen(brush, max(1.2, width * s))
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPath(path)
    p.setOpacity(_clamp(opacity))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(fill))
    p.drawPath(path)
    p.save()
    p.setClipPath(path)
    sheen = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    sheen.setColorAt(0.0, _alpha("#FFFFFF", 0.07))
    sheen.setColorAt(0.30, _alpha("#FFFFFF", 0.0))
    sheen.setColorAt(1.0, _alpha(BLUE, 0.05))
    p.fillRect(rect, sheen)
    p.setOpacity(_clamp(0.11 * bright * opacity))
    p.setPen(QPen(brush, 16.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.restore()
    p.setOpacity(_clamp((0.35 + 0.5 * bright) * opacity))
    p.setPen(QPen(brush, 1.5))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.setOpacity(1.0)


# --------------------------------------------------------------------------- #
# The morph's timing (shared with client/gui/game_pane.py)
# --------------------------------------------------------------------------- #
def morph_progress(phase: str, ms: float, reduced: bool = False) -> tuple[float, float, float, float]:
    """(e, swell, wobble, opacity) of the glass `ms` into `phase`: e = 0 the
    orb's ellipse (times `swell`), 1 the pane; a negative e is the breath at
    the end of a close (the ellipse a little larger, settling). Reduced
    motion: a plain fade, the pane's full shape from the first frame."""
    if phase == "open":
        return 1.0, 1.0, 0.0, 1.0
    if phase == "closed":
        return 0.0, 1.0, 0.0, 0.0
    if reduced:
        u = _clamp(ms / FADE_MS)
        return 1.0, 1.0, 0.0, (u if phase == "opening" else 1.0 - u)
    if phase == "opening":
        # the drop forms (the head fills out, its edge starts to move), then stretches
        form = _smooth(ms / 90)
        e = _ease_out((ms - 90) / 470) if ms > 90 else 0.0
        swell = 1.0 + 0.45 * form * (1.0 - e)
        wobble = form * (1.0 - _smooth((ms - 380) / 180))
        return e, swell, wobble, 1.0
    # closing: the shape waits for the content to fade, pulls back, then one breath
    v = _clamp((ms - 120) / 420)
    e = 1.0 - _smooth(v)
    if ms <= 540:
        wobble = _smooth((ms - 120) / 120) * (1.0 - _smooth((ms - 460) / 80))
        return e, 1.0 + 0.45 * (1.0 - e) * _smooth(v), wobble, 1.0
    breath = math.sin(math.pi * _clamp((ms - 540) / 120))  # the drop settles: a small overshoot
    return -0.06 * breath, 1.0, 0.0, 1.0


def content_opacity(phase: str, ms: float, reduced: bool = False) -> float:
    """The content inside the glass: fades in over the last 180 ms of an
    open, out over the first 140 ms of a close (with the whole, reduced)."""
    length = OPEN_MS if phase == "opening" else CLOSE_MS
    if reduced:
        u = _clamp(ms / FADE_MS)
        return u if phase == "opening" else 1.0 - u
    if phase == "opening":
        return _smooth((ms - (length - 180)) / 180)
    return 1.0 - _smooth(ms / 140)


def face_opacity(phase: str, ms: float, reduced: bool = False) -> float:
    """How much of Ombri's face still shows on the drop: 1 at the very start
    of an open and the very end of a close, 0 in between."""
    if reduced or phase not in ("opening", "closing"):
        return 0.0
    if phase == "opening":
        return 1.0 - _smooth(ms / FACE_MS)
    return _smooth((ms - (CLOSE_MS - FACE_MS)) / FACE_MS)


# --------------------------------------------------------------------------- #
# The layer grid
# --------------------------------------------------------------------------- #
_FALLBACK_COLORS = ("#8A919E", "#B8945F", "#6B4F2A", "#CFE8F3", "#9C7A45", "#7A8F6B", "#B0646A", "#6F7FB8")


def merge_materials(materials: list) -> list[dict]:
    """The step card's rows, one per block: a guide may list the same block
    under two palette keys with different states (stairs upside down for
    the trim, a top and a bottom slab) and the player buys "12 escadas",
    not 7 and 5 - so rows with the same block id become one, their counts
    added up (the server's build_guide.materials already does this; an
    older server's rows, or a hand-made message, are merged here). A row
    without a block id stays its own. The first row's place and colour."""
    out: list[dict] = []
    by_block: dict[str, dict] = {}
    for m in materials or []:
        if not isinstance(m, dict):
            continue
        bid = block_id(m.get("block")) if m.get("block") else ""
        hit = by_block.get(bid) if bid else None
        if hit is None:
            row = dict(m)
            row["chars"] = [str(c) for c in (m.get("chars") or [m.get("char")]) if c is not None]
            out.append(row)
            if bid:
                by_block[bid] = row
            continue
        for key in ("layer", "total", "placed", "left"):
            try:
                hit[key] = int(hit.get(key) or 0) + int(m.get(key) or 0)
            except (TypeError, ValueError):
                pass
        hit["chars"] += [str(c) for c in (m.get("chars") or [m.get("char")]) if c is not None and str(c) not in hit["chars"]]
    return out


def card_order(rows: list[dict]) -> list[dict]:
    """The step card's order: the blocks this layer needs first, the one
    with the most to place at the top, then the rest - the rows that say
    "0 nesta camada", dimmed - in the server's order (the whole build's
    total). The server keeps its order and the spoken lines; on a late
    layer (the watchtower's 10th) the player would otherwise scroll past
    four finished blocks to find the logs and lanterns. An older server's
    rows without `layer` keep their order."""
    def key(row: dict) -> tuple[int, int]:
        try:
            here = int(row.get("layer") or 0)
        except (TypeError, ValueError):
            here = 0
        return (0, -here) if here > 0 else (1, 0)
    return sorted(rows, key=key)  # a stable sort: ties keep the server's order


def block_colors(palette: dict) -> dict[str, QColor]:
    """char -> colour for every block of a build's palette (air left out). A
    block without a colour in its JSON gets one of a short fixed list, so two
    such blocks still tell apart."""
    colors: dict[str, QColor] = {}
    i = 0
    for ch, spec in sorted(palette.items()):
        if not isinstance(spec, dict):
            continue
        raw = str(spec.get("color") or "")
        c = QColor(raw) if raw else QColor()
        if not c.isValid():
            c = QColor(_FALLBACK_COLORS[i % len(_FALLBACK_COLORS)])
            i += 1
        colors[str(ch)] = c
    return colors


def layer_rows(build: dict) -> list[list[str]]:
    """The layers' rows, bottom-up, each `{"rows": [...], "hint": {...}}` or
    bare rows - the file as the server loaded it (server/build_guide.py)."""
    rows = []
    for layer in build.get("layers") or []:
        raw = layer.get("rows") if isinstance(layer, dict) else layer
        rows.append([str(r) for r in (raw or [])])
    return rows


class LayerGrid(QWidget):
    """One layer of the build seen from above: rows back to front (the front
    row at the bottom). Blocks of this layer are lit in their colour; air over
    a block of the layer below shows that block's dim footprint, so the walls
    are seen while the roof goes on. No letters in the cells (Claude's review
    of PR #27: they read like debug output) - the colour and the chips say the
    block, and hovering a cell names it. A layer change ripples out from the
    centre (~340 ms); a faint slow shimmer plays on the glass while shown."""

    def __init__(self) -> None:
        super().__init__()
        self._layers: list[list[str]] = []
        self._colors: dict[str, QColor] = {}
        self._names: dict[str, str] = {}
        self._lang = "pt"
        self.setMouseTracking(True)
        self._layer = 1
        self._from: int | None = None
        self._t_change = 0.0
        self._cell = CELL_MAX
        self._cols = self._rows = 0
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_build(self, build: dict, max_width: int, lang: str = "pt") -> None:
        self._layers = layer_rows(build)
        self._colors = block_colors(build.get("palette") or {})
        self._lang = lang
        self._names = {}
        for ch, spec in (build.get("palette") or {}).items():
            if isinstance(spec, dict):
                forms = (spec.get("names") or {}).get(lang) or (spec.get("names") or {}).get("en") or []
                self._names[str(ch)] = str(forms[0] if forms else spec.get("block") or ch)
        self._rows = max((len(r) for r in self._layers), default=0)
        self._cols = max((len(row) for r in self._layers for row in r), default=0)
        self._cell = max(8, min(CELL_MAX, (max_width - 16) // max(1, self._cols)))
        self.setFixedSize(self._cols * self._cell + 16, self._rows * self._cell + 16)
        self._from = None
        self.update()

    def set_layer(self, layer: int, animate: bool = True) -> None:
        layer = max(1, min(layer, len(self._layers))) if self._layers else 1
        if layer == self._layer:
            return
        if animate and not reduced_motion() and self.isVisible():
            self._from, self._t_change = self._layer, time.monotonic()
            self._timer.start(FRAME_MS)
        else:
            self._from = None
        self._layer = layer
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802 - the shimmer plays only while shown
        if not reduced_motion():
            self._timer.start(SHIMMER_MS)
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def _tick(self) -> None:
        if self._from is not None and time.monotonic() - self._t_change > LAYER_MS / 1000 + 0.05:
            self._from = None
            self._timer.start(SHIMMER_MS)  # back to the slow shimmer
        self.update()

    def _cell_at(self, layer: int, r: int, c: int) -> str:
        rows = self._layers[layer - 1] if 1 <= layer <= len(self._layers) else []
        return rows[r][c] if r < len(rows) and c < len(rows[r]) else "."

    def cell_name(self, pos) -> str:
        """The block under `pos` (widget coordinates): this layer's, or the
        layer below's as "<name> (camada N)" where this layer is air."""
        c = int((pos.x() - 8) // self._cell) if self._cell else -1
        r = int((pos.y() - 8) // self._cell) if self._cell else -1
        if not (0 <= r < self._rows and 0 <= c < self._cols):
            return ""
        ch = self._cell_at(self._layer, r, c)
        if ch in self._names:
            return self._names[ch]
        below = self._cell_at(self._layer - 1, r, c)
        if below in self._names and self._layer > 1:
            return f"{self._names[below]} ({ui(self._lang, 'guide_layer_caps').lower()} {self._layer - 1})"
        return ""

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        name = self.cell_name(event.position())
        if name:
            QToolTip.showText(event.globalPosition().toPoint(), name, self)
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        QToolTip.hideText()
        super().leaveEvent(event)

    def _draw_cell(self, p: QPainter, rect: QRectF, ch: str, below: str, alpha: float, shimmer: float) -> None:
        if alpha <= 0.01:
            return
        radius = max(2.0, self._cell * 0.22)
        p.setPen(Qt.PenStyle.NoPen)
        if ch in self._colors:
            col = self._colors[ch].lighter(100 + round(shimmer * 100))
            p.setBrush(QBrush(_alpha(col, 0.28 * alpha)))
            p.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), radius + 2, radius + 2)  # the glow
            p.setBrush(QBrush(_alpha(col, 0.95 * alpha)))
            p.drawRoundedRect(rect, radius, radius)
            top = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            top.setColorAt(0.0, _alpha("#FFFFFF", 0.22 * alpha))
            top.setColorAt(0.5, _alpha("#FFFFFF", 0.0))
            p.setBrush(QBrush(top))
            p.drawRoundedRect(rect, radius, radius)
        elif below in self._colors:
            p.setBrush(QBrush(_alpha(self._colors[below], 0.20 * alpha)))
            p.drawRoundedRect(rect, radius, radius)
        else:
            p.setBrush(QBrush(_alpha("#FFFFFF", (0.028 + 0.02 * shimmer) * alpha)))
            p.drawRoundedRect(rect, radius, radius)

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._layers:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = time.monotonic() - self._t0
        plate = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_alpha("#FFFFFF", 0.035)))
        p.drawRoundedRect(plate, 14, 14)
        cell = self._cell
        gap = max(1.0, cell * 0.12)
        cx, cy = (self._cols - 1) / 2, (self._rows - 1) / 2
        max_dist = math.hypot(cx, cy) or 1.0
        shimmer_on = self._timer.isActive() and not reduced_motion()
        progress = (time.monotonic() - self._t_change) * 1000 / LAYER_MS if self._from is not None else 1.0
        for r in range(self._rows):
            for c in range(self._cols):
                rect = QRectF(8 + c * cell + gap / 2, 8 + r * cell + gap / 2, cell - gap, cell - gap)
                shimmer = 0.06 * math.sin(1.6 * t + 0.9 * c + 0.6 * r) if shimmer_on else 0.0
                ch, below = self._cell_at(self._layer, r, c), self._cell_at(self._layer - 1, r, c)
                if self._from is None:
                    self._draw_cell(p, rect, ch, below, 1.0, shimmer)
                    continue
                delay = math.hypot(c - cx, r - cy) / max_dist * 0.35  # the ripple: the centre first
                a = _smooth((progress - delay) / 0.65)
                old_ch, old_below = self._cell_at(self._from, r, c), self._cell_at(self._from - 1, r, c)
                self._draw_cell(p, rect, old_ch, old_below, 1.0 - a, shimmer)
                self._draw_cell(p, rect, ch, below, a, shimmer)
        p.end()


# --------------------------------------------------------------------------- #
# Chips, buttons, the ripple layer
# --------------------------------------------------------------------------- #
def _drawn_part(img: QImage) -> QImage:
    """The icon without its transparent margin: the owner's crops are 60x60
    with the block drawn as the inventory shows it - a slab in the lower
    half, a ladder tall and narrow, a torch a few pixels wide. The chip
    draws this part centred, at the size it fits, never stretched."""
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = img.width(), img.height()
    if w < 1 or h < 1 or img.bytesPerLine() != w * 4:
        return img
    alpha = bytes(img.constBits())[3::4]  # one byte a pixel, rows end to end
    rows = [y for y in range(h) if max(alpha[y * w:(y + 1) * w]) > 16]
    cols = [x for x in range(w) if max(alpha[x::w]) > 16]
    if not rows or not cols:
        return img
    return img.copy(cols[0], rows[0], cols[-1] - cols[0] + 1, rows[-1] - rows[0] + 1)


def fit_icon(icon: QPixmap, side: int, ratio: float = 1.0) -> QPixmap:
    """`icon` (its drawn part) on a transparent `side` x `side` square, as
    large as it fits with its own proportions, centred - a cube fills the
    square, a slab is a wide bar across its middle, a torch a thin stick.
    Drawn at `ratio` pixels a point so a HiDPI screen gets a sharp one."""
    ratio = max(1.0, float(ratio or 1.0))
    px = max(1, int(round(side * ratio)))
    out = QPixmap(px, px)
    out.fill(Qt.GlobalColor.transparent)
    if not icon.isNull():
        scaled = icon.scaled(px, px, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap((px - scaled.width()) // 2, (px - scaled.height()) // 2, scaled)
        p.end()
    out.setDevicePixelRatio(ratio)
    return out


class _Assets:
    """The Build pane's pictures for the run (client/build_assets.py keeps the
    bytes in main.py; here they are decoded as needed): `icons` block id ->
    QPixmap, `shots` (guide id, name) -> JPEG bytes, decoded into a few
    QImages at a time (a 1280 px picture is ~3.4 MB decoded; the bytes are
    ~120 KB). Shared by the window's pane and the in-game pane. A picture
    that doesn't decode is dropped."""

    ICON_PX = 22
    MAX_SHOTS = 128     # bytes kept: a few builds of 5-12 layers plus their angles, ~16 MB at most
    MAX_DECODED = 6     # QImages kept: the step on screen and its neighbours

    def __init__(self) -> None:
        self.icons: dict[str, QPixmap] = {}
        self.shots: dict[tuple[str, str], bytes] = {}
        self._decoded: dict[tuple[str, str], QImage] = {}

    @staticmethod
    def _bytes(data) -> bytes | None:
        if isinstance(data, str):
            try:
                return base64.b64decode(data, validate=True)
            except (ValueError, TypeError):
                return None
        return bytes(data) if isinstance(data, (bytes, bytearray)) else None

    def put_icons(self, icons: dict) -> list[str]:
        """{block id: base64 PNG} -> the ids that are new here (for a refresh)."""
        done: list[str] = []
        for bid, data in (icons or {}).items():
            if not isinstance(bid, str) or block_id(bid) != bid or bid in self.icons:
                continue
            raw = self._bytes(data)
            if not raw:
                continue
            img = QImage.fromData(raw)
            if img.isNull():
                continue
            self.icons[bid] = QPixmap.fromImage(_drawn_part(img))
            done.append(bid)
        return done

    def put_shots(self, guide_id: str, shots: dict) -> list[str]:
        """{name: base64 JPEG} of one build -> the names that are new here."""
        done: list[str] = []
        if not guide_id:
            return done
        for name, data in (shots or {}).items():
            if not isinstance(name, str) or not valid_shot(guide_id, name) or (guide_id, name) in self.shots:
                continue
            raw = self._bytes(data)
            if not raw or QImage.fromData(raw).isNull():
                continue
            if len(self.shots) >= self.MAX_SHOTS:  # the other builds' shots go first; this build keeps its own
                for key in [k for k in self.shots if k[0] != guide_id]:
                    del self.shots[key]
                    self._decoded.pop(key, None)
                if len(self.shots) >= self.MAX_SHOTS:
                    self.shots.clear()
                    self._decoded.clear()
            self.shots[(guide_id, name)] = raw
            done.append(name)
        return done

    def take_message_shot(self, msg: dict) -> list[str]:
        """The `shot` inside a `guide` message: into the cache before the pane shows the step."""
        shot = msg.get("shot") if isinstance(msg, dict) else None
        if isinstance(shot, dict) and shot.get("image"):
            return self.put_shots(str(msg.get("id") or ""), {str(shot.get("name") or ""): shot["image"]})
        return []

    def icon(self, block) -> QPixmap | None:
        return self.icons.get(block_id(block))

    def shot_bytes(self, guide_id: str | None, name: str) -> bytes | None:
        return self.shots.get((guide_id or "", name)) if guide_id else None

    def shot(self, guide_id: str | None, name: str) -> QImage | None:
        key = (guide_id or "", name)
        raw = self.shots.get(key) if guide_id else None
        if raw is None:
            return None
        img = self._decoded.get(key)
        if img is None:
            img = QImage.fromData(raw)
            if img.isNull():
                self.shots.pop(key, None)
                return None
            if len(self._decoded) >= self.MAX_DECODED:
                self._decoded.clear()
            self._decoded[key] = img
        return img

    def layer_shot(self, guide_id: str | None, layer: int) -> QImage | None:
        return self.shot(guide_id, f"layer_{int(layer)}")

    def clear(self) -> None:
        self.icons.clear()
        self.shots.clear()
        self._decoded.clear()


ASSETS = _Assets()


class _Shot(QWidget):
    """The step's real screenshot: the picture fitted to the pane's width,
    rounded, with a thin light rim - the look of the in-game pane's picture.
    Hidden when the step has none (the grid alone, as before)."""

    RADIUS = 12

    def __init__(self) -> None:
        super().__init__()
        self.image = QImage()
        self._scaled: QPixmap | None = None
        self._scaled_for = QSize()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(10)

    def set_image(self, image: QImage | None) -> None:
        self.image = image if image is not None else QImage()
        self._scaled = None
        self.setVisible(not self.image.isNull())
        self._fit()
        self.update()

    def _fit(self) -> None:
        if self.image.isNull() or self.width() < 20:
            return
        h = int(round(self.width() * self.image.height() / max(1, self.image.width())))
        if h != self.height():
            self.setFixedHeight(max(10, h))

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._fit()
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        if self.image.isNull():
            return
        box = self.rect()
        if self._scaled is None or self._scaled_for != box.size():
            self._scaled = QPixmap.fromImage(self.image.scaled(box.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                               Qt.TransformationMode.SmoothTransformation))
            self._scaled_for = box.size()
        pm = self._scaled
        target = QRect(box.x() + (box.width() - pm.width()) // 2, box.y() + (box.height() - pm.height()) // 2,
                       pm.width(), pm.height())
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(target), self.RADIUS, self.RADIUS)
        p.setClipPath(path)
        p.drawPixmap(target, pm)
        p.setClipping(False)
        p.setPen(QPen(_alpha("#FFFFFF", 0.10), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(target).adjusted(0.5, 0.5, -0.5, -0.5), self.RADIUS, self.RADIUS)
        p.end()


class _ElideLabel(QLabel):
    """A label that shortens its text with an ellipsis instead of widening its
    row (the chips must never push the pane wider than the window)."""

    def __init__(self, size: int, weight: int = 400, color: str = TEXT) -> None:
        super().__init__()
        self._full = ""
        self.setStyleSheet(f"font-size: {size}px; font-weight: {weight}; color: {color};")
        self.setMinimumWidth(24)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_full(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._elide()
        super().resizeEvent(event)

    def _elide(self) -> None:
        metrics = self.fontMetrics()
        self.setText(metrics.elidedText(self._full, Qt.TextElideMode.ElideRight, max(10, self.width() - 2)))


class _Chip(QFrame):
    """One material, a glass row: the block's icon (the owner's inventory crop,
    server/build_guides/icons) - or its colour while there is none - and its
    name on the left; on the right this layer's count over what is left of
    the total."""

    def __init__(self) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setFixedHeight(46)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 5, 14, 5)
        row.setSpacing(10)
        self.swatch = QLabel()
        self.swatch.setFixedSize(_Assets.ICON_PX, _Assets.ICON_PX)
        self.swatch.setAlignment(Qt.AlignmentFlag.AlignCenter)  # the icon sits in the middle of its square, its own shape
        row.addWidget(self.swatch, 0, Qt.AlignmentFlag.AlignVCenter)
        self.name = _ElideLabel(13, 600)
        row.addWidget(self.name, 1)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.here = _label(size=12, weight=600)
        self.here.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.left = _label(size=11, color=TEXT_2)
        self.left.setAlignment(Qt.AlignmentFlag.AlignRight)
        col.addWidget(self.here)
        col.addWidget(self.left)
        row.addLayout(col, 0)
        self._color = QColor(TEXT_3)
        self._dim = False
        self.block = ""

    def set_material(self, m: dict, color: QColor, lang: str) -> None:
        self._color = color
        here, left, total = int(m.get("layer") or 0), int(m.get("left") or 0), int(m.get("total") or 0)
        self._dim = here == 0
        self.block = str(m.get("block") or "")
        self.name.set_full(str(m.get("name") or m.get("block") or m.get("char") or ""))
        self.here.setText(ui(lang, "guide_this_layer", n=here))
        self.left.setText(ui(lang, "guide_all_placed", total=total) if left <= 0
                          else ui(lang, "guide_left", left=left, total=total))
        self.refresh_icon()
        self.name.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {TEXT_3 if self._dim else TEXT};")
        self.here.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {TEXT_3 if self._dim else ACCENT};")
        self.left.setStyleSheet(f"font-size: 11px; color: {TEXT_3 if self._dim else TEXT_2};")
        self.update()

    def refresh_icon(self) -> None:
        """The block's icon when the cache has it (now, or once it lands),
        else the colour square - dimmed with the row."""
        icon = ASSETS.icon(self.block)
        if icon is not None:
            self.swatch.setStyleSheet("background: transparent; border: none;")
            self.swatch.setPixmap(fit_icon(icon, _Assets.ICON_PX, self.devicePixelRatioF()))
            effect = QGraphicsOpacityEffect(self.swatch)
            effect.setOpacity(0.45 if self._dim else 1.0)
            self.swatch.setGraphicsEffect(effect)
            return
        self.swatch.setGraphicsEffect(None)
        self.swatch.setPixmap(QPixmap())
        swatch = _alpha(self._color, 0.45 if self._dim else 1.0)
        self.swatch.setStyleSheet(f"background: rgba({swatch.red()}, {swatch.green()}, {swatch.blue()}, {swatch.alphaF():.2f});"
                                  f" border-radius: 5px; border: 1px solid {self._color.lighter(135).name()};"
                                  f" margin: 3px;")

    def paintEvent(self, event) -> None:  # noqa: N802 - a glass chip, no hard border
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_alpha("#FFFFFF", 0.045)))
        p.drawRoundedRect(rect, 11, 11)
        sheen = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        sheen.setColorAt(0.0, _alpha("#FFFFFF", 0.05))
        sheen.setColorAt(0.6, _alpha("#FFFFFF", 0.0))
        p.setBrush(QBrush(sheen))
        p.drawRoundedRect(rect, 11, 11)
        if not self._dim:
            p.setPen(QPen(QBrush(_alpha(self._color, 0.35)), 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(rect, 11, 11)
        p.end()


class RoundButton(QPushButton):
    """A round glass button with a stroke icon (back / next / close)."""

    def __init__(self, icon: str, size: int = 48) -> None:
        super().__init__()
        self._icon = icon
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("background: transparent; border: none;")
        self._hover = False

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        on = self.isEnabled()
        p.setOpacity(1.0 if on else 0.35)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_alpha("#FFFFFF", 0.10 if (self._hover and on) else 0.06)))
        p.drawEllipse(rect)
        p.setPen(QPen(_rim(rect), 1.3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setOpacity((0.9 if self._hover else 0.6) if on else 0.25)
        p.drawEllipse(rect)
        p.setOpacity(1.0 if on else 0.4)
        n = round(rect.width() * 0.42)
        pix = line_icon(self._icon, TEXT, n).pixmap(n, n)
        p.drawPixmap(QPointF(rect.center().x() - n / 2, rect.center().y() - n / 2), pix)
        p.end()


class _RippleLayer(QWidget):
    """Rings that spread from the small orb across the pane when a voice
    command lands - the orb's own ripple, on the glass. Mouse-transparent,
    over the content."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._origin = QPointF(0, 0)
        self._t0: float | None = None
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

    def start(self, origin: QPointF) -> None:
        if reduced_motion() or not self.isVisible():
            return
        self._origin, self._t0 = origin, time.monotonic()
        self.raise_()
        self._timer.start(FRAME_MS)

    @property
    def alive(self) -> bool:
        return self._t0 is not None

    def _tick(self) -> None:
        if self._t0 is not None and (time.monotonic() - self._t0) * 1000 > RIPPLE_MS:
            self._t0 = None
            self._timer.stop()
        self.update()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._t0 = None
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._t0 is None:
            return
        u = _clamp((time.monotonic() - self._t0) * 1000 / RIPPLE_MS)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        reach = math.hypot(self.width(), self.height())
        brush = _rim(QRectF(self.rect()))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for lag, weight in ((0.0, 1.0), (0.14, 0.55)):
            v = _clamp((u - lag) / (1.0 - lag))
            if v <= 0.0:
                continue
            r = _ease_out(v) * reach
            pen = QPen(brush, 2.0 + 10.0 * (1.0 - v))
            p.setPen(pen)
            p.setOpacity(0.45 * weight * (1.0 - v))
            p.drawEllipse(self._origin, r, r)
        p.end()


class _BuildRow(QPushButton):
    """One build in the list page: its name and size, a glass row to click."""

    def __init__(self, build: dict, lang: str) -> None:
        super().__init__()
        self.guide_id = str(build.get("id") or "")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(58)
        self.setStyleSheet("background: transparent; border: none; text-align: left;")
        col = QVBoxLayout(self)
        col.setContentsMargins(16, 9, 16, 9)
        col.setSpacing(2)
        name = _label(str(build.get("name") or self.guide_id), size=15, weight=600)
        size = build.get("size") if isinstance(build.get("size"), dict) else {}
        sub = _label(ui(lang, "guide_list_sub", n=int(build.get("layers") or 0), x=size.get("x", "?"),
                        y=size.get("y", "?"), z=size.get("z", "?")), size=12, color=TEXT_2)
        for lab in (name, sub):
            lab.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            col.addWidget(lab)
        self._hover = False

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_alpha("#FFFFFF", 0.085 if self._hover else 0.05)))
        p.drawRoundedRect(rect, 14, 14)
        p.setOpacity(0.55 if self._hover else 0.25)
        p.setPen(QPen(_rim(rect), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 14, 14)
        p.setOpacity(1.0)
        n = 14
        p.drawPixmap(QPointF(rect.right() - 26, rect.center().y() - n / 2),
                     line_icon("chevron_right", TEXT_2, n).pixmap(n, n))
        p.end()


# --------------------------------------------------------------------------- #
# The pane's content
# --------------------------------------------------------------------------- #
class BuildPane(QWidget):
    """The content inside the glass: a view only; `GuideMorph` holds the state
    and feeds it (`show_build`, `show_layer`, `show_list`)."""
    next_clicked = Signal()
    back_clicked = Signal()
    close_clicked = Signal()
    build_clicked = Signal(str)

    def __init__(self, parent: QWidget, small_orb: QWidget) -> None:
        super().__init__(parent)
        self.lang = "pt"
        self.keys = {"ask": "F8", "note": "F6", "pad": None}
        self.small_orb = small_orb
        self._chips: list[_Chip] = []
        self._colors: dict[str, QColor] = {}
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        col = QVBoxLayout(self)
        col.setContentsMargins(18, 14, 18, 14)
        col.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        small_orb.setParent(self)
        head.addWidget(small_orb, 0, Qt.AlignmentFlag.AlignVCenter)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.caps = _label(name="caps")
        self.title = _label(size=15, weight=600)
        titles.addWidget(self.caps)
        titles.addWidget(self.title)
        head.addLayout(titles, 1)
        self.number = QLabel()
        self.number.setTextFormat(Qt.TextFormat.RichText)
        self.number.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.number)
        self.close_btn = RoundButton("close", 34)
        self.close_btn.clicked.connect(self.close_clicked.emit)
        head.addWidget(self.close_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(head)

        self.pages = QStackedWidget()
        self.pages.setStyleSheet("background: transparent;")
        col.addWidget(self.pages, 1)

        # Page 0: the build.
        build_page = QWidget()
        bcol = QVBoxLayout(build_page)
        bcol.setContentsMargins(0, 0, 0, 0)
        bcol.setSpacing(8)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        host = QWidget()
        self._hcol = hcol = QVBoxLayout(host)
        hcol.setContentsMargins(0, 0, 4, 0)
        hcol.setSpacing(8)
        self.shot = _Shot()  # the step's real screenshot (server/build_shots.py), hidden when there is none
        self.shot.hide()
        self.grid = LayerGrid()
        self.front = _label(name="caps")
        self.front.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.hint = _label(size=14, color=TEXT_2)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.hint.setWordWrap(True)
        self.chips_host = QWidget()
        self.chips_col = QVBoxLayout(self.chips_host)
        self.chips_col.setContentsMargins(0, 2, 0, 0)
        self.chips_col.setSpacing(4)
        self._with_shot: bool | None = None
        self._build: dict | None = None
        self._arrange(False)
        self.scroll.setWidget(host)
        bcol.addWidget(self.scroll, 1)
        nav = QHBoxLayout()
        nav.setSpacing(10)
        self.back_btn = RoundButton("chevron_left")
        self.next_btn = RoundButton("chevron_right")
        self.back_btn.clicked.connect(self.back_clicked.emit)
        self.next_btn.clicked.connect(self.next_clicked.emit)
        nav.addWidget(self.back_btn)
        mid = QVBoxLayout()
        mid.setSpacing(2)
        self.voice_hint = _label(size=12, color=TEXT_2)
        self.voice_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.voice_hint.setWordWrap(True)
        self.keys_hint = _label(size=11, color=TEXT_3)
        self.keys_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        mid.addWidget(self.voice_hint)
        mid.addWidget(self.keys_hint)
        nav.addLayout(mid, 1)
        nav.addWidget(self.next_btn)
        bcol.addLayout(nav)
        self.pages.addWidget(build_page)

        # Page 1: the list of builds.
        list_page = QWidget()
        lcol = QVBoxLayout(list_page)
        lcol.setContentsMargins(0, 6, 0, 0)
        lcol.setSpacing(10)
        self.list_title = _label(size=20, weight=600, heading=True)
        self.list_title.setWordWrap(True)
        lcol.addWidget(self.list_title)
        self.list_hint = _label(size=13, color=TEXT_2)
        self.list_hint.setWordWrap(True)
        lcol.addWidget(self.list_hint)
        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_host = QWidget()
        self.list_col = QVBoxLayout(self.list_host)
        self.list_col.setContentsMargins(0, 0, 4, 0)
        self.list_col.setSpacing(8)
        self.list_col.addStretch(1)
        self.list_scroll.setWidget(self.list_host)
        lcol.addWidget(self.list_scroll, 1)
        self.list_status = _label(size=13, color=TEXT_2)
        self.list_status.setWordWrap(True)
        lcol.addWidget(self.list_status)
        self.pages.addWidget(list_page)

        self.ripple = _RippleLayer(self)
        self.ripple.raise_()
        self._texts()

    # ---- texts ----------------------------------------------------------
    def set_language(self, lang: str, keys: dict) -> None:
        self.lang, self.keys = lang, dict(keys)
        self._texts()

    def _texts(self) -> None:
        L = self.lang
        self.caps.setText(ui(L, "guide_caps"))
        self.front.setText("▼ " + ui(L, "guide_front"))
        self.close_btn.setToolTip(ui(L, "guide_close"))
        self.back_btn.setToolTip(ui(L, "guide_back"))
        self.voice_hint.setText(ui(L, "guide_voice_hint", ask=self.keys.get("ask", "F8")))
        self.keys_hint.setText(ui(L, "guide_keys_hint"))
        self.list_title.setText(ui(L, "guide_list_title"))
        self.list_hint.setText(ui(L, "guide_list_hint", ask=self.keys.get("ask", "F8")))

    # ---- the build page -------------------------------------------------
    GRID_W_WITH_SHOT = 260  # the grid under the chips when the step has its picture: smaller cells

    def _arrange(self, with_shot: bool) -> None:
        """The step card's order. With the real screenshot (the brief's card):
        the picture, the hint, the chips, then the grid - smaller - for the
        exact pattern. Without one: the grid first, as before."""
        if with_shot == self._with_shot:
            return
        self._with_shot = with_shot
        col = self._hcol
        order = (self.shot, self.hint, self.chips_host, self.grid, self.front) if with_shot \
            else (self.shot, self.grid, self.front, self.hint, self.chips_host)
        for w in order:
            col.removeWidget(w)  # Qt deletes the item itself: a takeAt() item PySide drops later would keep
        while col.count():  # the widget's size cache on the dead item, and the new one would never refresh
            col.takeAt(0)  # (the hint stayed one line high after a longer text); the stretch goes
        for w in order:
            if w is self.grid:
                col.addWidget(w, 0, Qt.AlignmentFlag.AlignHCenter)
            else:
                col.addWidget(w)
        col.addStretch(1)
        if self._build is not None:
            self._fit_grid()

    def _fit_grid(self) -> None:
        if self._build is None:
            return
        width = self.width() - 44 if self.width() > 100 else 360
        if self._with_shot:
            width = min(width, self.GRID_W_WITH_SHOT)
        layer = self.grid._layer
        self.grid.set_build(self._build, width, self.lang)
        if layer:
            self.grid.set_layer(layer, animate=False)

    def show_build(self, name: str, build: dict) -> None:
        self.title.setText(name)
        self._colors = block_colors(build.get("palette") or {})
        self._build = build
        self._fit_grid()
        self.pages.setCurrentIndex(0)

    def show_layer(self, layer: int, total: int, materials: list[dict], hint: str, animate: bool,
                   shot: QImage | None = None) -> None:
        L = self.lang
        self.shot.set_image(shot)
        self._arrange(shot is not None and not shot.isNull())
        self.grid.set_layer(layer, animate)
        self.number.setText(f"<span style='font-family:\"{HEADING_FAMILY}\"; font-size:34px; font-weight:600;"
                            f" color:{ACCENT}'>{layer}</span>"
                            f"<span style='font-family:\"{HEADING_FAMILY}\"; font-size:18px; color:{TEXT_2}'> / {total}</span>")
        self.hint.setText(hint)
        self.hint.setVisible(bool(hint))
        self.number.setToolTip(f"{ui(L, 'guide_layer_caps').capitalize()} {layer} / {total}")
        self.back_btn.setEnabled(layer > 1)
        self.next_btn.setToolTip(ui(L, "guide_finish" if layer >= total else "guide_next"))
        materials = card_order(merge_materials(materials))  # one chip per block, this layer's blocks first
        while len(self._chips) < len(materials):
            chip = _Chip()
            self._chips.append(chip)
            self.chips_col.addWidget(chip)
        for i, chip in enumerate(self._chips):
            if i < len(materials):
                m = materials[i]
                chip.set_material(m, self._colors.get(str(m.get("char")), QColor(TEXT_3)), L)
                chip.show()
            else:
                chip.hide()

    def refresh_icons(self) -> None:
        """Icons that landed after the chips were drawn."""
        for chip in self._chips:
            if not chip.isHidden():
                chip.refresh_icon()

    # ---- the list page --------------------------------------------------
    def show_list(self, builds: list[dict], game: str, error: str = "") -> None:
        L = self.lang
        self.title.setText(game or "")
        self.number.setText("")
        while self.list_col.count() > 1:  # keep the trailing stretch
            item = self.list_col.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)
                item.widget().deleteLater()
        for i, b in enumerate(builds):
            row = _BuildRow(b, L)
            row.clicked.connect(lambda _=False, gid=row.guide_id: self._pick(gid))
            self.list_col.insertWidget(i, row)
        if error:
            self.list_status.setText(ui(L, "guide_list_error", error=error))
        elif not builds:
            self.list_status.setText(ui(L, "guide_list_empty", game=game) if game else ui(L, "guide_list_empty_nogame"))
        else:
            self.list_status.setText("")
        self.pages.setCurrentIndex(1)

    def _pick(self, guide_id: str) -> None:
        self.list_status.setText(ui(self.lang, "guide_loading"))
        self.build_clicked.emit(guide_id)

    def show_error(self, error: str) -> None:
        self.list_status.setText(ui(self.lang, "guide_list_error", error=error))

    def resizeEvent(self, event) -> None:  # noqa: N802
        self.ripple.setGeometry(self.rect())
        super().resizeEvent(event)

    def orb_centre(self) -> QPointF:
        return QPointF(self.small_orb.mapTo(self, self.small_orb.rect().center()))


# --------------------------------------------------------------------------- #
# The overlay: the orb becomes the pane
# --------------------------------------------------------------------------- #
class GuideMorph(QWidget):
    """The Build pane's keeper: the state (build, layer, listing), the glass
    shape and its morph from and back to the orb. `apply()` takes the
    server's `guide` message; the buttons and keys move without a turn."""
    state_changed = Signal(str, int, bool)  # guide id ("" when none), layer, listing
    open_requested = Signal(str)  # a build clicked in the list: main.py fetches it and sends `open`
    closed = Signal(bool)  # the pane closed (True = the last layer was finished)
    finished = Signal(str, str)  # the last layer was finished: guide id, name (the in-game pane shows the front shot)
    phase_changed = Signal(str)  # opening | open | closing | closed: the window hides its orb meanwhile

    def __init__(self, parent: QWidget, small_orb: QWidget) -> None:
        super().__init__(parent)
        self.guide_id: str | None = None
        self.name = ""
        self.layer = 0
        self.total = 0
        self.build: dict | None = None
        self.materials_by_layer: list[list[dict]] = []
        self.hints: list[str] = []
        self.listing = False
        self.game = ""
        self.phase = "closed"  # closed | opening | open | closing
        self._t0 = 0.0
        self._origin = QPointF(0, 0)
        self._radii = (34.0, 30.0)
        self._done = False
        self._pending_open: tuple | None = None
        self._clock = time.monotonic()
        self.pane = BuildPane(self, small_orb)
        self.pane.hide()
        self._fade = QGraphicsOpacityEffect(self.pane)
        self._fade.setOpacity(1.0)
        self._fade.setEnabled(False)
        self.pane.setGraphicsEffect(self._fade)
        self.pane.next_clicked.connect(lambda: self.step(1))
        self.pane.back_clicked.connect(lambda: self.step(-1))
        self.pane.close_clicked.connect(lambda: self.close_guide(False))
        self.pane.build_clicked.connect(self.open_requested.emit)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self.hide()

    # ---- state --------------------------------------------------------------
    @property
    def active(self) -> bool:
        """Something to show (a build, the list, or a morph still running)."""
        return self.phase != "closed"

    @property
    def small_orb(self) -> QWidget:
        return self.pane.small_orb

    def state(self) -> dict:
        """What the next turn_start.guide should say."""
        if self.guide_id and self.phase in ("opening", "open"):
            return {"id": self.guide_id, "layer": self.layer}
        if self.listing and self.phase in ("opening", "open"):
            return {"list": True}
        return {}

    def set_language(self, lang: str, keys: dict) -> None:
        self.pane.set_language(lang, keys)
        if self.build is not None and self.phase in ("opening", "open"):
            self._show_layer(self.layer, animate=False)

    def set_game(self, game: str | None) -> None:
        self.game = game or ""

    def pane_rect(self) -> QRectF:
        w, h = (self.width(), self.height()) if self.width() >= 100 else self.sizeHint().toTuple()
        return QRectF(PANE_MARGIN, 4, w - 2 * PANE_MARGIN, h - PANE_MARGIN - 4)

    def _emit_state(self) -> None:
        self.state_changed.emit(self.guide_id or "", self.layer if self.guide_id else 0,
                                bool(self.listing and not self.guide_id))

    # ---- the guide messages -------------------------------------------------
    def apply(self, msg: dict, origin: tuple[QPointF, float, float] | None = None, animate: bool = True) -> None:
        """A `guide` message (server/build_guide.py `message()`, or main.py's
        REST click-open in the same shape): open / layer / close / list, plus
        the UI-only `error` and `step` (a word heard on the PC). `origin` = (centre, rx, ry) of the orb in this
        widget's coordinates, where the drop starts; `animate` False when no
        one would see the morph (another view, the window in the tray): the
        pane settles at once and shows when the view comes back."""
        action = str(msg.get("action") or "")
        spoken = str(msg.get("command") or "") not in ("", "click")
        self.pane.setGeometry(self.pane_rect().toRect())
        if action in ("open", "layer"):
            ASSETS.take_message_shot(msg)  # the step's real screenshot rides with the message
        if action == "open":
            build = msg.get("build")
            if not isinstance(build, dict):
                return
            self.guide_id = str(msg.get("id") or build.get("id") or "")
            self.name = str(msg.get("name") or self.guide_id)
            self.build = build
            self.listing = False
            self._done = False
            by_layer = msg.get("materials_by_layer")
            self.materials_by_layer = [list(m) for m in by_layer] if isinstance(by_layer, list) else []
            hints = msg.get("hints")
            self.hints = [str(h or "") for h in hints] if isinstance(hints, list) else []
            self.total = int(msg.get("layers") or len(layer_rows(build)) or 1)
            layer = int(msg.get("layer") or 1)
            self.layer = max(1, min(layer, self.total))
            if not self.materials_by_layer and isinstance(msg.get("materials"), list):
                self.materials_by_layer = [list(msg["materials"])] * self.total
            self.pane.show_build(self.name, build)
            self._show_layer(self.layer, animate=False)
            self._open(origin, animate)
            if spoken:
                self.pulse()
        elif action == "layer":
            if self.build is None or self.phase not in ("opening", "open"):
                return
            if str(msg.get("id") or self.guide_id) != self.guide_id:
                return
            layer = int(msg.get("layer") or self.layer)
            self._show_layer(max(1, min(layer, self.total)), animate=True)
            if spoken:
                self.pulse()
        elif action == "step":
            # A word heard on the PC (client/voice_commands.py, listening mode
            # on): "próxima" / "volta" said alone. Moves as the keys do - next
            # on the last layer finishes the build - with the spoken command's
            # ripple; `step()` emits the state itself.
            if self.build is None or self.phase not in ("opening", "open"):
                return
            try:
                delta = int(msg.get("delta") or 0)
            except (TypeError, ValueError):
                delta = 0
            if delta:
                self.step(1 if delta > 0 else -1)
            if spoken and self.build is not None:
                self.pulse()
            return
        elif action == "close":
            if self.phase in ("opening", "open"):
                self.close_guide(bool(msg.get("done")), animate)  # emits the state itself
            return
        elif action == "list":
            builds = msg.get("builds")
            self.guide_id, self.build, self.layer, self.total = None, None, 0, 0
            self.listing = True
            self.pane.show_list([b for b in (builds if isinstance(builds, list) else []) if isinstance(b, dict)],
                                str(msg.get("game") or self.game), str(msg.get("error") or ""))
            self._open(origin, animate)
            if spoken:
                self.pulse()
        elif action == "error":
            self.pane.show_error(str(msg.get("error") or "?"))
            return  # nothing moved
        self._emit_state()

    def _show_layer(self, layer: int, animate: bool) -> None:
        self.layer = layer
        materials = self.materials_by_layer[layer - 1] if layer - 1 < len(self.materials_by_layer) else []
        hint = self.hints[layer - 1] if layer - 1 < len(self.hints) else ""
        self.pane.show_layer(layer, self.total, materials, hint, animate, ASSETS.layer_shot(self.guide_id, layer))

    def guide_assets(self, msg: dict) -> None:
        """A `guide_assets` message (main.py, client/build_assets.py): icons
        and shots into the shared cache; the step on screen is redrawn when
        it gains its picture or an icon."""
        icons = ASSETS.put_icons(msg.get("icons") if isinstance(msg.get("icons"), dict) else {})
        gid = str(msg.get("id") or "")
        shots = ASSETS.put_shots(gid, msg.get("shots") if isinstance(msg.get("shots"), dict) else {})
        if self.build is None or self.phase not in ("opening", "open"):
            return
        if icons:
            self.pane.refresh_icons()
        if shots and gid == self.guide_id and f"layer_{self.layer}" in shots:
            self._show_layer(self.layer, animate=False)  # the step gains its picture

    # ---- the buttons and keys ----------------------------------------------
    def step(self, delta: int) -> None:
        """Next / back without a turn. Next on the last layer finishes the
        build and closes the pane, as the server's own rule does."""
        if self.build is None or self.phase not in ("opening", "open"):
            return
        target = self.layer + delta
        if target > self.total:
            self.close_guide(True)
            return
        if target < 1:
            return
        self._show_layer(target, animate=True)
        self._emit_state()

    def go(self, layer: int) -> None:
        if self.build is None or self.phase not in ("opening", "open") or not 1 <= layer <= self.total:
            return
        self._show_layer(layer, animate=True)
        self._emit_state()

    def handle_key(self, event) -> bool:
        """The window's keys while the pane shows: arrows / PageUp / PageDown
        move, Home / End jump, 1-9 jump, Esc closes. True when used."""
        if self.phase not in ("opening", "open"):
            return False
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close_guide(False)
            return True
        if self.build is None:
            return False
        if key in (Qt.Key.Key_Right, Qt.Key.Key_PageDown, Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.step(1)
            return True
        if key in (Qt.Key.Key_Left, Qt.Key.Key_PageUp, Qt.Key.Key_Backspace):
            self.step(-1)
            return True
        if key == Qt.Key.Key_Home:
            self.go(1)
            return True
        if key == Qt.Key.Key_End:
            self.go(self.total)
            return True
        if Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
            self.go(key - Qt.Key.Key_0)
            return True
        return False

    def close_guide(self, done: bool = False, animate: bool = True) -> None:
        if self.phase not in ("opening", "open"):
            return
        self._done = done
        if done and self.guide_id:
            self.finished.emit(self.guide_id, self.name)
        self.guide_id, self.build, self.layer, self.total, self.listing = None, None, 0, 0, False
        self.materials_by_layer, self.hints = [], []
        self._emit_state()
        self._start("closing", animate and self.isVisible())
        self.closed.emit(done)

    def pulse(self) -> None:
        """A voice command landed: a ripple from the small orb across the pane."""
        if self.phase == "open" and self.isVisible():
            self.pane.ripple.start(self.pane.orb_centre())

    # ---- the morph ----------------------------------------------------------
    def _open(self, origin: tuple[QPointF, float, float] | None, animate: bool) -> None:
        if self.phase in ("opening", "open"):
            return  # a new build inside an open pane: the content changed, the glass stays
        if origin is not None:
            self._origin, self._radii = origin[0], (origin[1], origin[2])
        else:
            rect = self.pane_rect()
            self._origin, self._radii = QPointF(rect.center().x(), rect.top() + 70), (34.0, 30.0)
        self.pane.hide()
        self._start("opening", animate)

    def _start(self, phase: str, animate: bool) -> None:
        """`animate` False: no one would see it (another view, the tray) -
        settle at once. The window shows and hides this overlay with its
        views; a close hides it itself when done."""
        self.phase = phase
        self._t0 = time.monotonic()
        self.phase_changed.emit(phase)
        if not animate:
            self._finish()
            return
        self.show()
        self.raise_()
        self._timer.start(FRAME_MS)
        self._tick()

    def _finish(self) -> None:
        self._timer.stop()
        if self.phase == "opening":
            self.phase = "open"
            self._fade.setEnabled(False)
            self._fade.setOpacity(1.0)
            self.pane.setGeometry(self.pane_rect().toRect())
            self.pane.show()
            self.pane.raise_()
            self.pane.setFocus()
        elif self.phase == "closing":
            self.phase = "closed"
            self.pane.hide()
            self.hide()
        self.update()
        self.phase_changed.emit(self.phase)

    def _tick(self) -> None:
        if self.phase not in ("opening", "closing"):
            self._timer.stop()
            return
        length = OPEN_MS if self.phase == "opening" else CLOSE_MS
        if reduced_motion():
            length = FADE_MS
        ms = (time.monotonic() - self._t0) * 1000
        if ms >= length:
            self._finish()
            return
        a = content_opacity(self.phase, ms, reduced_motion())
        if a > 0.0:
            if not self.pane.isVisible():
                self.pane.setGeometry(self.pane_rect().toRect())
                self.pane.show()
            self._fade.setEnabled(True)
            self._fade.setOpacity(a)
        else:
            self.pane.hide()
        self.update()

    def shape_progress(self, now: float | None = None) -> tuple[float, float, float, float]:
        """(e, swell, wobble, opacity) of the glass at `now` - `morph_progress()`
        for this morph's phase and clock."""
        now = time.monotonic() if now is None else now
        return morph_progress(self.phase, (now - self._t0) * 1000, reduced_motion())

    def paintEvent(self, event) -> None:  # noqa: N802
        if self.phase == "closed":
            return
        e, swell, wobble, opacity = self.shape_progress()
        t = time.monotonic() - self._clock
        pane = self.pane_rect()
        rx, ry = self._radii
        if e < 0.0:  # the breath at the end of a close: the orb's ellipse, a little larger
            scale = 1.0 - e * 2.5
            path = QPainterPath()
            path.addEllipse(self._origin, rx * scale, ry * scale)
            e = 0.0
        else:
            path = blob_path(self._origin, rx, ry, pane, PANE_RADIUS, e, wobble, t, swell)
        p = QPainter(self)
        p.setOpacity(opacity)
        bright = 0.55 + 0.35 * math.sin(math.pi * e) if e < 1.0 else 0.55
        paint_glass(p, path, bright, _mix(BODY, GLASS, e))
        face = self._face_opacity()
        if face > 0.0 and indicator.STYLE == "shadow":
            # The real orb is hidden the moment the drop forms: its face is drawn
            # here over the glass, melting away (or back) - one shape, no cut.
            # Through an image layer: draw_shadow() sets its own opacities.
            s_ = rx / HEAD_RX
            box = s_ / 0.9
            box_centre = QPointF(self._origin.x(), self._origin.y() - HEAD_CY * s_)
            v = QPointF(self.mapFromGlobal(QCursor.pos())) - box_centre  # the eyes keep following the cursor
            dist = max(1.0, math.hypot(v.x(), v.y()))
            reach = min(1.0, dist / 250.0) / dist
            side = int(box * 1.8)  # room for the glow around the head
            dpr = self.devicePixelRatioF()
            layer = QImage(int(side * dpr), int(side * dpr), QImage.Format.Format_ARGB32_Premultiplied)
            layer.setDevicePixelRatio(dpr)
            layer.fill(0)
            q = QPainter(layer)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            indicator.draw(q, QPointF(side / 2, side / 2), box, self.small_orb.state, time.monotonic() - self._clock,
                           look=(v.x() * reach, v.y() * reach))
            q.end()
            p.setOpacity(opacity * face)
            p.drawImage(QPointF(box_centre.x() - side / 2, box_centre.y() - side / 2), layer)
        p.end()

    def _face_opacity(self) -> float:
        return face_opacity(self.phase, (time.monotonic() - self._t0) * 1000, reduced_motion())

    def hideEvent(self, event) -> None:  # noqa: N802 - nothing moves while the window is hidden
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        if self.phase in ("opening", "closing"):
            self._timer.start(FRAME_MS)
        elif self.phase == "open":
            self.pane.setGeometry(self.pane_rect().toRect())
            self.pane.show()
        super().showEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        if self.phase == "open":
            self.pane.setGeometry(self.pane_rect().toRect())
        super().resizeEvent(event)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(416, 680)
