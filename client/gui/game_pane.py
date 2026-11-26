"""client/gui/game_pane.py — the in-game pane: what Ombri answers visually, in
one small window that grows out of the in-game orb, so the player never
leaves the game (the owner's brief, docs/agents/arena_handoff.md 2026-11-24;
visual pane PR 2 of 4).

What is here
- `GamePane`: a second top-level window with exactly the orb's behaviour
  (client/gui/overlay.py: frameless, translucent, topmost, no taskbar entry,
  never activates - WS_EX_NOACTIVATE - click-through with the orb on Scroll
  Lock, the recordings setting, topmost re-asserted every second). Closed, it
  is hidden and the orb is as always. Opening is the Build pane's own morph
  (client/gui/build_pane.py: the orb's disc swells, its edge goes liquid, the
  one shape stretches into the rounded glass; the content fades in once the
  shape has settled; the reverse on close, ending with one breath before the
  real orb is shown again) - the same `morph_progress()` timing, `blob_path()`
  and `paint_glass()`, so both panes move alike. The orb window hides the
  moment the drop forms and comes back where the pane's small orb was.
- Picture-in-picture, small: 1/20 of the screen area by default (430x242 on
  1920x1080, 16:9), resized from the corner away from the orb between 1/40
  and 1/8 of the area; dragged exactly like the orb (the orb comes back where
  the pane was left). Size and orb position are remembered on the player's
  PC (client/gui/pane_store.py), nothing else.
- Two contents, what exists today: the layer guide (`_GuideView`: the same
  `LayerGrid` as the window's Build pane, this layer's materials, the hint)
  and a plain picture (`_ImageView`: the image the server sent, its title,
  the site and creator, a button to the source page). One state: the guide
  here is the window's Build pane's - `GuideMorph` keeps it, this pane
  follows (`follow_guide`), and its own buttons ask the keeper (`guide_step`,
  `guide_close_requested`). The pictures come in a `visual` message (PR 3
  sends it; the shape is in the handoff); "pin" keeps one up while they build
  and after a restart.
- Buttons for when the cursor is free (Esc, the inventory, the click-through
  key): back / next, pin, an arrow back to the orb (the pane folds, its
  content is kept; a click on the orb or "show" brings it back), close.
  Voice is the main control in game: the pane's state goes up to main.py
  (`state_changed` -> "pane_state:" -> turn_start.visual) so the server's
  matcher can act on "outra", "essa", "fecha" with the pane in mind.
- Every picture shown here is also in the chat (`visual_shown` -> a card
  with its thumbnail and source): the pane is a view of the conversation.
- Motion: client/gui/motion.py `enabled()` and build_pane's reduced motion
  (both make every move a plain fade); animate only on a change, then stop;
  no timers while idle (the small orb's own timer runs only while it
  listens, thinks or speaks).
- Contextual hints (client/gui/hints.py, owner 2026-11-24): the first time
  pictures open here, the first guide step, the first pin - one text line
  over the content, fading on its own, "não mostrar de novo" tick, at most
  a few times per hint id (remembered in hints.json next to pane.json).
- True exclusive fullscreen hides any window: `fullscreen_exclusive()` asks
  Windows once when the pane opens and the pane says so upward
  (`hidden_by_fullscreen`, once per run) so main.py can tell the player
  instead of failing silently.
- The real screenshots (PR 3b, server/build_shots.py): a guide step whose
  picture the pane has (build_pane.ASSETS, fed by the `guide` message's
  `shot` and main.py's `guide_assets`) shows it where the grid was - the
  small pane has room for one of the two, and the owner's screenshot of the
  layer says more than the grid; the grid stays for a step without one.
  When the last layer is finished (`show_finished`, the keeper's
  `finished`), the pane shows the finished build's front shot as a
  picture ("CONSTRUÇÃO PRONTA"), pinnable like any other. Library pictures
  with real shots come as three items (front, side, back) that share a
  title: the caps line names the angle (`view`).
"""
from __future__ import annotations

import ctypes
import math
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QStackedWidget, QWidget

from client.gui import chat_view, hints as hints_mod, indicator, motion, pane_store
from client.gui import overlay as ov
from client.gui.build_pane import (ASSETS, CELL_MAX, CLOSE_MS, FADE_MS, FRAME_MS, GLASS, OPEN_MS, LayerGrid,
                                   RoundButton, _alpha, _mix, _rim, _RippleLayer, blob_path, block_colors, card_order,
                                   content_opacity, face_opacity, layer_rows, merge_materials, morph_progress,
                                   paint_glass, reduced_motion)
from client.gui.fonts import BODY_FAMILY, HEADING_FAMILY
from client.gui.orb import draw_sphere, state_color
from client.gui.shadow import BODY
from client.gui.ui_texts import ui
from client.gui.widgets import ACCENT, TEXT, TEXT_2, TEXT_3, _label

DEFAULT_AREA = 1 / 20   # of the screen's area (owner: "we don't want to occupy a large space")
MIN_AREA = 1 / 40
MAX_AREA = 1 / 8
ASPECT = 16 / 9
MIN_ASPECT, MAX_ASPECT = 1.0, 2.4
MARGIN = 16        # transparent room around the glass for its glow, inside the window
PAD = 10           # the content's inset from the glass edge
HEADER_H = 36
ORB_SLOT = 28      # the small live orb's box
BTN = 26
GRIP = 18          # the resize corner
RADIUS = 18        # the glass corners (the window's Build pane uses 24 on a bigger shape)
GLOW_SCALE = 70.0  # paint_glass's glow scale: a little tighter than the big pane's, the window is small
QUERY_MAX = 120
STYLE_MAX = 8
MAX_ITEMS = 12
SPIN_STATES = ("ouvindo", "pensando", "pesquisando", "falando")

_fullscreen_probe = None  # a test sets a callable; None asks Windows
_told_fullscreen = False


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #
def pane_size_for(screen: QSize, fraction: float = DEFAULT_AREA) -> QSize:
    """A 16:9 pane covering `fraction` of the screen's area."""
    area = max(1, screen.width() * screen.height()) * fraction
    w = math.sqrt(area * ASPECT)
    return QSize(int(round(w)), int(round(w / ASPECT)))


def clamp_pane_size(w: int, h: int, screen: QSize) -> QSize:
    """Between 1/40 and 1/8 of the screen's area, an aspect between 1:1 and
    2.4:1, and inside the screen with the glow's margin."""
    w, h = max(1.0, float(w)), max(1.0, float(h))
    aspect = w / h
    if aspect < MIN_ASPECT * 0.995:
        h = w / MIN_ASPECT
    elif aspect > MAX_ASPECT * 1.005:
        h = w / MAX_ASPECT
    screen_area = max(1, screen.width() * screen.height())
    area = w * h
    lo, hi = screen_area * MIN_AREA, screen_area * MAX_AREA
    if area < lo * 0.995:  # (half a percent of slack: a rounded size clamps to itself)
        k = math.sqrt(lo / area)
        w, h = w * k, h * k
    elif area > hi * 1.005:
        k = math.sqrt(hi / area)
        w, h = w * k, h * k
    w = min(w, screen.width() - 2 * MARGIN)
    h = min(h, screen.height() - 2 * MARGIN)
    return QSize(int(round(w)), int(round(h)))


def fullscreen_exclusive() -> bool:
    """True when a Direct3D exclusive-fullscreen app owns the display
    (SHQueryUserNotificationState == QUNS_RUNNING_D3D_FULL_SCREEN): no window
    of ours can show over it. Borderless and windowed games say BUSY or
    ACCEPTS_NOTIFICATIONS. Anything odd = False (never a false alarm)."""
    if _fullscreen_probe is not None:
        try:
            return bool(_fullscreen_probe())
        except Exception:  # noqa: BLE001
            return False
    if sys.platform != "win32":
        return False
    try:
        state = ctypes.c_int(0)
        if ctypes.WinDLL("shell32").SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return False
        return state.value == 3  # QUNS_RUNNING_D3D_FULL_SCREEN
    except (OSError, AttributeError, ValueError):
        return False


def _reduced() -> bool:
    return reduced_motion() or not motion.enabled()


def _elide(label: QLabel, text: str) -> None:
    label.setToolTip(text if len(text) > 24 else "")
    label.setText(label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(20, label.width() - 2)))


# --------------------------------------------------------------------------- #
# The small live orb in the pane's corner
# --------------------------------------------------------------------------- #
class CalmOrb(QWidget):
    """The in-game look of the orb (client/gui/shadow.py `calm`: a dark disc
    with the neon rim; the sphere when that is the picked look), small, in
    the pane's header. Its timer runs only while Ombri listens, thinks or
    speaks - at rest nothing moves (owner, 2026-11-05)."""

    def __init__(self, box: int = ORB_SLOT) -> None:
        super().__init__()
        self.setFixedSize(box, box)
        self.state = "idle"
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self.update)

    def set_state(self, state: str) -> None:
        self.state = state
        self._sync_timer()
        self.update()

    def _sync_timer(self) -> None:
        if self.state in SPIN_STATES and self.isVisible() and motion.enabled():
            if not self._timer.isActive():
                self._timer.start(FRAME_MS)
        else:
            self._timer.stop()

    def showEvent(self, event) -> None:  # noqa: N802
        self._sync_timer()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def shape(self) -> tuple[QPointF, float, float]:
        """(centre, rx, ry) of the body inside this widget, where a morph ends."""
        c = QPointF(self.width() / 2, self.height() / 2)
        r = 0.36 * self.width() * 0.92  # draw_shadow(calm=True): a disc of 0.36 * box; the sphere is drawn alike
        return c, r, r

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        box = self.width() * 0.92
        t = time.monotonic() - self._t0
        if indicator.custom():
            indicator.draw(p, c, box, self.state, t, level=0.5 + 0.5 * math.sin(2 * math.pi * t / 0.55), calm=True)
        else:
            draw_sphere(p, c, box * 0.36, state_color(self.state), glow=0.8)
        p.end()


# --------------------------------------------------------------------------- #
# The guide content: the layer grid, the materials, the hint
# --------------------------------------------------------------------------- #
class _MaterialList(QWidget):
    """This layer's materials as short rows: a colour dot, the name, the count
    on this layer and what is left of the total - painted, no widgets, so a
    resize costs nothing. Rows that don't fit end in a "+N"."""

    ROW = 18

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[tuple[QColor, str, int, int, int]] = []
        self.setMinimumWidth(60)

    def set_materials(self, materials: list[dict], colors: dict[str, QColor]) -> None:
        self._rows = []
        for m in card_order(merge_materials(materials)):  # one row per block, this layer's blocks first
            name = str(m.get("name") or m.get("block") or m.get("char") or "")
            here, left, total = int(m.get("layer") or 0), int(m.get("left") or 0), int(m.get("total") or 0)
            self._rows.append((colors.get(str(m.get("char")), QColor(TEXT_3)), name, here, left, total))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._rows:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fits = max(1, self.height() // self.ROW)
        shown = self._rows if len(self._rows) <= fits else self._rows[:max(1, fits - 1)]
        fm = p.fontMetrics()
        y = 0
        for color, name, here, left, _total in shown:
            dim = here == 0
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(_alpha(color, 0.45 if dim else 1.0)))
            p.drawEllipse(QPointF(6, y + self.ROW / 2), 4.5, 4.5)
            count = f"{here}"
            rest = f" · {left}" if left > 0 else ""  # this layer's count, then what is left of the total
            w_count, w_rest = fm.horizontalAdvance(count), fm.horizontalAdvance(rest)
            right_w = w_count + w_rest + 4
            p.setPen(QColor(TEXT_3 if dim else TEXT))
            p.drawText(QRectF(16, y, max(10, self.width() - 16 - right_w), self.ROW), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(name, Qt.TextElideMode.ElideRight, max(10, self.width() - 16 - right_w)))
            x0 = self.width() - right_w
            p.setPen(QColor(TEXT_3 if dim else ACCENT))
            p.drawText(QRectF(x0, y, w_count, self.ROW), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, count)
            if rest:
                p.setPen(QColor(TEXT_3))
                p.drawText(QRectF(x0 + w_count, y, w_rest, self.ROW), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, rest)
            y += self.ROW
        if len(shown) < len(self._rows):
            p.setPen(QColor(TEXT_3))
            p.drawText(QRectF(16, y, self.width() - 16, self.ROW), Qt.AlignmentFlag.AlignVCenter,
                       f"+{len(self._rows) - len(shown)}")
        p.end()


class _PinButton(RoundButton):
    """The pin, with a lit dot while the picture is pinned."""

    def __init__(self) -> None:
        super().__init__("pin", BTN)
        self.lit = False

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self.lit:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(ACCENT))
            p.drawEllipse(QPointF(self.width() - 5.5, 5.5), 3.0, 3.0)
            p.end()


def paint_fitted(p: QPainter, image: QImage, box: QRect, cache: list, radius: int = 10) -> None:
    """The picture fitted inside `box`, centred, rounded, with a thin light
    rim. `cache` is a two-slot list [QPixmap | None, QSize] the caller keeps,
    so a repaint at the same size scales nothing."""
    if cache[0] is None or cache[1] != box.size():
        cache[0] = QPixmap.fromImage(image.scaled(box.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation))
        cache[1] = box.size()
    pm = cache[0]
    target = QRect(box.x() + (box.width() - pm.width()) // 2, box.y() + (box.height() - pm.height()) // 2,
                   pm.width(), pm.height())
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(QRectF(target), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(target, pm)
    p.setClipping(False)
    p.setPen(QPen(_alpha("#FFFFFF", 0.10), 1.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(target).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


class _ShotBox(QWidget):
    """A step's real screenshot, fitted in whatever box the view gives it."""

    def __init__(self) -> None:
        super().__init__()
        self.image = QImage()
        self._cache: list = [None, QSize()]

    def set_image(self, image: QImage | None) -> None:
        self.image = image if image is not None else QImage()
        self._cache = [None, QSize()]
        self.update()

    def aspect(self) -> float:
        return self.image.width() / max(1, self.image.height()) if not self.image.isNull() else 16 / 9

    def paintEvent(self, event) -> None:  # noqa: N802
        if self.image.isNull():
            return
        p = QPainter(self)
        paint_fitted(p, self.image, self.rect(), self._cache)
        p.end()


class _GuideView(QWidget):
    """The layer guide at pane size: the step's real screenshot - or, without
    one, the grid (square, as tall as the pane allows) - on the left, the
    hint and the materials on the right."""

    def __init__(self) -> None:
        super().__init__()
        self.grid = LayerGrid()
        self.grid.setParent(self)
        self.shot = _ShotBox()
        self.shot.setParent(self)
        self.shot.hide()
        self.hint = _label(size=12, color=TEXT_2)
        self.hint.setParent(self)
        self.hint.setWordWrap(True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.materials = _MaterialList()
        self.materials.setParent(self)
        self.build: dict | None = None
        self.lang = "pt"
        self._colors: dict[str, QColor] = {}
        self._mats: list[dict] = []

    def set_build(self, build: dict, lang: str) -> None:
        self.build, self.lang = build, lang
        self._colors = block_colors(build.get("palette") or {})
        self._fit_grid()

    def set_layer(self, layer: int, materials: list[dict], hint: str, animate: bool,
                  shot: QImage | None = None) -> None:
        self.grid.set_layer(layer, animate)
        self._mats = list(materials)
        self.materials.set_materials(self._mats, self._colors)
        self.hint.setText(hint)
        self.hint.setVisible(bool(hint))
        self.set_shot(shot)

    def set_shot(self, shot: QImage | None) -> None:
        """The step's screenshot takes the grid's place while there is one."""
        self.shot.set_image(shot)
        has = not self.shot.image.isNull()
        self.shot.setVisible(has)
        self.grid.setVisible(not has)
        self._place()

    @property
    def has_shot(self) -> bool:
        return not self.shot.image.isNull()

    def _fit_grid(self) -> None:
        if self.build is None or self.width() < 40:
            return
        rows = layer_rows(self.build)
        n_rows = max((len(r) for r in rows), default=1) or 1
        n_cols = max((len(row) for r in rows for row in r), default=1) or 1
        side_w = int(self.width() * 0.56)
        cell = max(5, min(CELL_MAX, (side_w - 16) // n_cols, (self.height() - 16) // n_rows))
        layer = self.grid._layer
        self.grid.set_build(self.build, cell * n_cols + 16, self.lang)
        self.grid.set_layer(layer, animate=False)
        self._place()

    def _place(self) -> None:
        if self.has_shot:
            # the picture: up to 60% of the width, as tall as the pane allows, its own aspect
            sw = min(int(self.width() * 0.60), int(self.height() * self.shot.aspect()))
            sh = int(sw / self.shot.aspect())
            self.shot.setGeometry(0, max(0, (self.height() - sh) // 2), max(20, sw), max(12, sh))
            x = self.shot.width() + 10
        else:
            g = self.grid
            g.move(0, max(0, (self.height() - g.height()) // 2))
            x = g.width() + 10
        w = max(20, self.width() - x)
        hint_h = 0
        if self.hint.text() and not self.hint.isHidden():  # (isVisible() is False while the pane is still hidden)
            hint_h = min(self.height() // 2, self.hint.heightForWidth(w) or 16)
            self.hint.setGeometry(x, 0, w, hint_h)
        self.materials.setGeometry(x, hint_h + (6 if hint_h else 0), w, max(0, self.height() - hint_h - (6 if hint_h else 0)))

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._fit_grid()
        self._place()
        super().resizeEvent(event)


# --------------------------------------------------------------------------- #
# The picture content
# --------------------------------------------------------------------------- #
class _ImageView(QWidget):
    """The picture, fitted and rounded, with the credit under it (the site
    and the creator) and a button to the source page. The player's browser
    opens only on that click (chat_view.open_url, the same door as a card)."""

    CREDIT_H = 20

    def __init__(self) -> None:
        super().__init__()
        self.image = QImage()
        self._cache: list = [None, QSize()]  # the scaled pixmap and the size it was made for
        self.url = ""
        self.credit = _label(size=11, color=TEXT_2)
        self.credit.setParent(self)
        self.link = RoundButton("link", 22)
        self.link.setParent(self)
        self.link.clicked.connect(self._open_source)
        self.link.hide()
        self._credit_text = ""

    def set_item(self, item: dict, image: QImage, lang: str) -> None:
        self.image = image
        self._cache = [None, QSize()]
        self.url = item.get("source_url") or ""
        parts = [item.get("site") or ""]
        if item.get("author"):
            parts.append(ui(lang, "pane_by", author=item["author"]))
        self._credit_text = " · ".join(p for p in parts if p)
        self.link.setVisible(bool(self.url))
        self.link.setToolTip(ui(lang, "pane_source") + (f"\n{self.url}" if self.url else ""))
        self._place()
        self.update()

    def _open_source(self) -> None:
        if not self.url:
            return
        try:
            chat_view.open_url(self.url)
        except Exception as exc:  # noqa: BLE001 - no browser is not a reason to lose the pane
            print(f"[pane] não consegui abrir {self.url!r}: {exc}", flush=True)

    def picture_rect(self) -> QRect:
        return QRect(0, 0, self.width(), max(10, self.height() - self.CREDIT_H - 4))

    def _place(self) -> None:
        y = self.height() - self.CREDIT_H
        link_w = 22 + 6 if self.link.isVisible() or self.url else 0
        self.credit.setGeometry(2, y, max(20, self.width() - link_w - 4), self.CREDIT_H)
        _elide(self.credit, self._credit_text)
        self.link.move(self.width() - 22, y - 1)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._place()
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        if self.image.isNull():
            return
        p = QPainter(self)
        paint_fitted(p, self.image, self.picture_rect(), self._cache)
        p.end()


# --------------------------------------------------------------------------- #
# The pane
# --------------------------------------------------------------------------- #
class GamePane(QWidget):
    """The in-game pane (module docstring). `orb` is the OverlayWindow it
    grows out of; `store_dir` the folder of pane.json / pin.* (None = the
    player's %APPDATA%/Parca; a test gives a scratch folder)."""

    state_changed = Signal(dict)            # what the next turn_start.visual should say (ui_process -> main.py)
    guide_step = Signal(int)                # back / next clicked here: the window's GuideMorph (the keeper) moves
    guide_close_requested = Signal()        # the X on a guide: the keeper closes it everywhere
    visual_shown = Signal(dict, object)     # (item meta, QImage): the chat mirrors it
    hidden_by_fullscreen = Signal()         # opened under exclusive fullscreen: say so (once per run)

    def __init__(self, orb: ov.OverlayWindow, store_dir: Path | None = None) -> None:
        super().__init__(None)
        self.orb = orb
        self.store_dir = store_dir
        self.lang = "pt"
        self.keys = {"ask": "F8", "note": "F6", "pad": None}
        self.phase = "closed"  # closed | opening | open | closing
        self.kind: str | None = None  # guide | image: what the glass shows (or would, folded)
        self.folded = False
        self.side = "right"  # the small orb's side in the header
        self._t0 = 0.0
        self._clock = time.monotonic()
        self._origin = QPointF(0, 0)  # the morph's orb centre, window coordinates
        self._radius = 16.0
        self._pane_size = QSize(430, 242)
        self._size_loaded = False
        self._native_setup_done = False
        self._click_through = False
        self._drag: tuple[str, QPoint, QRect] | None = None  # (mode, press pos, geometry at press)
        self._pending_show_after_close = False
        # the guide (a mirror of the window's GuideMorph: it keeps the state)
        self.guide_id: str | None = None
        self.guide_name = ""
        self.guide_layer = 0
        self.guide_total = 0
        self.guide_build: dict | None = None
        self.materials_by_layer: list[list[dict]] = []
        self.hints: list[str] = []
        self.coach = hints_mod.Hints(store_dir if store_dir is not None else pane_store.default_dir())
        # the pictures
        self.items: list[dict] = []
        self.images: list[QImage] = []
        self.index = 0
        self.query = ""
        self.style: list[str] = []
        self.pinned: dict | None = None
        self._mirrored: set = set()

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)

        # the content, over the glass
        self.body = QWidget(self)
        self.body.setStyleSheet(f'font-family: "{BODY_FAMILY}"; color: {TEXT}; background: transparent;')
        self.body.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._fade = QGraphicsOpacityEffect(self.body)
        self._fade.setOpacity(1.0)
        self._fade.setEnabled(False)
        self.body.setGraphicsEffect(self._fade)
        self.header = QWidget(self.body)
        self.small_orb = CalmOrb()
        self.caps = _label(size=10, weight=700, color=TEXT_3)
        self._caps_text = ""
        self.title = _label(size=13, weight=600)
        self.number = QLabel()
        self.number.setTextFormat(Qt.TextFormat.RichText)
        self.btn_prev = RoundButton("chevron_left", BTN)
        self.btn_next = RoundButton("chevron_right", BTN)
        self.btn_pin = _PinButton()
        self.btn_fold = RoundButton("collapse", BTN)
        self.btn_close = RoundButton("close", BTN)
        self.btn_prev.clicked.connect(lambda: self._nav(-1))
        self.btn_next.clicked.connect(lambda: self._nav(1))
        # (clicked carries `checked`; it must not land in the slots' `emit`)
        self.btn_pin.clicked.connect(lambda *_: self.toggle_pin())
        self.btn_fold.clicked.connect(lambda *_: self.fold())
        self.btn_close.clicked.connect(lambda *_: self.close_pane())
        row = QHBoxLayout(self.header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.small_orb, 0, Qt.AlignmentFlag.AlignVCenter)
        self.titles = QWidget(self.header)
        self.titles.setMinimumWidth(40)
        row.addWidget(self.titles, 1)
        row.addWidget(self.number, 0, Qt.AlignmentFlag.AlignVCenter)
        # previous / next in their own left-to-right pair: the row turns right-to-left when the
        # pane sits on the right, and the arrows must still read "< >" (2026-11-24 review)
        self.nav = QWidget(self.header)
        nav_row = QHBoxLayout(self.nav)
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.setSpacing(6)
        nav_row.setDirection(QHBoxLayout.Direction.LeftToRight)
        nav_row.addWidget(self.btn_prev, 0, Qt.AlignmentFlag.AlignVCenter)
        nav_row.addWidget(self.btn_next, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.nav, 0, Qt.AlignmentFlag.AlignVCenter)
        for b in (self.btn_pin, self.btn_fold, self.btn_close):
            row.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
        self.row = row
        self.caps.setParent(self.titles)
        self.title.setParent(self.titles)
        self.pages = QStackedWidget(self.body)
        self.pages.setStyleSheet("background: transparent;")
        self.guide_view = _GuideView()
        self.image_view = _ImageView()
        self.pages.addWidget(self.guide_view)
        self.pages.addWidget(self.image_view)
        self.ripple = _RippleLayer(self.body)
        self.body.hide()

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._topmost = QTimer(self)
        self._topmost.timeout.connect(lambda: ov.reassert_topmost(self))
        self.orb.moved.connect(self._orb_moved)
        self._texts()
        self.hide()

    # ---- the store ----------------------------------------------------------
    @property
    def layout_path(self) -> Path:
        return (self.store_dir or pane_store.default_dir()) / pane_store.LAYOUT_NAME

    def _load_size(self) -> None:
        if self._size_loaded:
            return
        self._size_loaded = True
        screen = self._screen_rect().size()
        self._pane_size = pane_size_for(screen)
        saved = pane_store.load_layout(self.layout_path)["size"]
        if saved:
            self._pane_size = clamp_pane_size(saved[0], saved[1], screen)

    def restore_pinned(self) -> bool:
        """At start (the first real game): the pinned picture comes back up,
        settling without the morph. True when there was one."""
        item = pane_store.load_pin(self.store_dir)
        if item is None:
            return False
        image = QImage.fromData(item["image"])
        if image.isNull():
            pane_store.clear_pin(self.store_dir)
            return False
        self.items, self.images, self.index = [item], [image], 0
        self.query, self.style = "", []
        self.pinned = item
        self.kind = "image"
        self._show_image(mirror=False)
        self._open(animate=False)
        self._emit_state()
        return True

    # ---- texts and state ----------------------------------------------------
    def set_language(self, lang: str, keys: dict | None = None) -> None:
        self.lang = lang
        if keys:
            self.keys = dict(keys)
        self._texts()
        if self.kind == "guide" and self.guide_build is not None:
            self._show_guide_layer(animate=False)
        elif self.kind == "image" and self.items:
            self._show_image(mirror=False)

    def _texts(self) -> None:
        L = self.lang
        self.btn_prev.setToolTip(ui(L, "pane_prev"))
        self.btn_next.setToolTip(ui(L, "pane_next"))
        self.btn_fold.setToolTip(ui(L, "pane_back"))
        self.btn_close.setToolTip(ui(L, "pane_close"))
        self.btn_pin.setToolTip(ui(L, "pane_unpin" if self.pinned is not None else "pane_pin"))
        self.btn_pin.lit = self.pinned is not None
        self.btn_pin.update()

    def set_state(self, state: str) -> None:
        self.small_orb.set_state(state)

    def set_click_through(self, enabled: bool) -> None:
        """With the orb: the whole pane lets clicks through to the game."""
        self._click_through = enabled
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, enabled)
        ov.set_native_click_through(self, enabled)
        self.update()

    @property
    def active(self) -> bool:
        return self.phase != "closed"

    @property
    def is_open(self) -> bool:
        return self.phase in ("opening", "open")

    def current_item(self) -> dict | None:
        return self.items[self.index] if 0 <= self.index < len(self.items) else None

    def state(self) -> dict:
        """What main.py mirrors into turn_start.visual: the pane's shape of the
        moment, never the bytes, never a question."""
        item = self.current_item()
        out: dict = {
            "open": self.is_open, "kind": self.kind if self.is_open else None, "folded": self.folded,
            "index": self.index if self.items else 0, "count": len(self.items),
            "pinned": self.pinned is not None, "title": item["title"] if item else "",
            "query": self.query, "style": list(self.style),
        }
        if self.guide_id:
            out["guide"] = {"id": self.guide_id, "layer": self.guide_layer}
        return out

    def _emit_state(self) -> None:
        self.state_changed.emit(self.state())

    # ---- the guide (the window's GuideMorph keeps it; this follows) ---------
    def guide_message(self, msg: dict) -> None:
        """Every `guide` message main.py sends the UI, before the window's
        GuideMorph gets it: `open` brings the build here, `layer` moves it,
        `close` closes it; `list` and `error` are the window's (nothing to
        click in game). `step` is the keeper's (it answers with its state)."""
        action = str(msg.get("action") or "")
        spoken = str(msg.get("command") or "") not in ("", "click")
        if action in ("open", "layer"):
            ASSETS.take_message_shot(msg)  # the step's real screenshot rides with the message
        if action == "open":
            build = msg.get("build")
            if not isinstance(build, dict):
                return
            self.guide_id = str(msg.get("id") or build.get("id") or "")
            self.guide_name = str(msg.get("name") or self.guide_id)
            self.guide_build = build
            by_layer = msg.get("materials_by_layer")
            self.materials_by_layer = [list(m) for m in by_layer] if isinstance(by_layer, list) else []
            hints = msg.get("hints")
            self.hints = [str(h or "") for h in hints] if isinstance(hints, list) else []
            self.guide_total = int(msg.get("layers") or len(layer_rows(build)) or 1)
            self.guide_layer = max(1, min(int(msg.get("layer") or 1), self.guide_total))
            if not self.materials_by_layer and isinstance(msg.get("materials"), list):
                self.materials_by_layer = [list(msg["materials"])] * self.guide_total
            self.kind = "guide"
            self.folded = False
            self.guide_view.set_build(build, self.lang)
            self._show_guide_layer(animate=False)
            self._open(animate=True)
            self.coach.show("pane_step", self.guide_view, self.lang)
            if spoken:
                self.pulse()
            self._emit_state()
        elif action == "layer":
            if self.guide_build is None or str(msg.get("id") or self.guide_id) != self.guide_id:
                return
            self.follow_guide(self.guide_id, int(msg.get("layer") or self.guide_layer), False)
            if spoken:
                self.pulse()
        elif action == "close":
            if self.guide_build is not None:
                self.follow_guide("", 0, False)

    def follow_guide(self, guide_id: str, layer: int, listing: bool) -> None:
        """The keeper's `state_changed`: the same build and layer here. An
        empty id = the guide closed (the window's Esc, the last layer done,
        the X here): this pane closes too when it was showing the guide."""
        if not guide_id:
            if self.guide_build is None:
                return
            self.guide_id, self.guide_build, self.guide_layer, self.guide_total = None, None, 0, 0
            self.materials_by_layer, self.hints = [], []
            if self.kind == "guide":
                if self.items:
                    self.kind = "image"
                    self._show_image(mirror=False)
                else:
                    self.kind = None
                    self._close(animate=True)
            self._emit_state()
            return
        if guide_id != self.guide_id or self.guide_build is None:
            return  # the build arrives with its `open` message, not here
        layer = max(1, min(int(layer), self.guide_total))
        if layer == self.guide_layer and self.kind == "guide":
            return
        self.guide_layer = layer
        if self.kind == "guide":
            self._show_guide_layer(animate=True)
        self._emit_state()

    def _show_guide_layer(self, animate: bool) -> None:
        L, n, total = self.lang, self.guide_layer, self.guide_total
        materials = self.materials_by_layer[n - 1] if n - 1 < len(self.materials_by_layer) else []
        hint = self.hints[n - 1] if n - 1 < len(self.hints) else ""
        self.guide_view.set_layer(n, materials, hint, animate and self.is_open, ASSETS.layer_shot(self.guide_id, n))
        self._set_caps(ui(L, "guide_caps"))
        _elide(self.title, self.guide_name)
        self.number.setText(f"<span style='font-family:\"{HEADING_FAMILY}\"; font-size:22px; font-weight:600;"
                            f" color:{ACCENT}'>{n}</span>"
                            f"<span style='font-family:\"{HEADING_FAMILY}\"; font-size:12px; color:{TEXT_2}'> / {total}</span>")
        self.number.setToolTip(ui(L, "pane_layer_short", n=n, m=total))
        self.btn_prev.setVisible(True)
        self.btn_next.setVisible(True)
        self.nav.setVisible(True)
        self.btn_prev.setEnabled(n > 1)
        self.btn_pin.hide()
        self.pages.setCurrentWidget(self.guide_view)
        self._place_content()

    def guide_assets(self, msg: dict) -> None:
        """A `guide_assets` message (main.py, client/build_assets.py): the
        pictures go into the shared cache (the window's keeper does the
        same; a second put of the same name costs nothing) and the step on
        screen gains its screenshot when it just arrived."""
        ASSETS.put_icons(msg.get("icons") if isinstance(msg.get("icons"), dict) else {})
        gid = str(msg.get("id") or "")
        ASSETS.put_shots(gid, msg.get("shots") if isinstance(msg.get("shots"), dict) else {})
        if self.kind != "guide" or self.guide_build is None or gid != self.guide_id or self.guide_view.has_shot:
            return
        shot = ASSETS.layer_shot(self.guide_id, self.guide_layer)
        if shot is not None:
            self.guide_view.set_shot(shot)

    def show_finished(self, guide_id: str, name: str) -> None:
        """The keeper's `finished` (the last layer done): the finished build's
        front shot stays up as a picture - the player sees what they built
        next to the real thing - when the pane has it and was showing the
        guide. The keeper's close (`follow_guide("")`) then leaves it be."""
        raw = ASSETS.shot_bytes(guide_id, "front")
        if raw is None or self.kind != "guide" or not self.is_open or guide_id != self.guide_id:
            return
        image = QImage.fromData(raw)
        if image.isNull():
            return
        item = pane_store.clean_item({"title": name or self.guide_name or guide_id, "kind": "done",
                                      "mime": "image/jpeg", "image": raw})
        if item is None:
            return
        self.items, self.images, self.index = [item], [image], 0
        self.query, self.style = "", []
        if self.pinned is not None and not self._same(item, self.pinned):
            self.pinned = None
            pane_store.clear_pin(self.store_dir)
        self.kind = "image"
        self._show_image(mirror=True)
        self._emit_state()

    # ---- the pictures (the `visual` message) --------------------------------
    def visual_message(self, msg: dict) -> None:
        """A `visual` message (main.py -> ui_process; PR 3's server sends it):
        `open` {items, index, query, style} shows the pictures; `index`
        {index} or {delta} moves; `pin` / `unpin`; `back` folds the pane;
        `show` unfolds it; `close` closes and forgets (the pin too)."""
        action = str(msg.get("action") or ("open" if msg.get("items") else ""))
        spoken = str(msg.get("command") or "") not in ("", "click")
        if action == "open":
            raw = msg.get("items") if isinstance(msg.get("items"), list) else []
            items, images = [], []
            for r in raw[:MAX_ITEMS]:
                item = pane_store.clean_item(r)
                if item is None:
                    continue
                image = QImage.fromData(item["image"])
                if image.isNull():
                    continue
                items.append(item)
                images.append(image)
            if not items:
                return
            self.items, self.images = items, images
            try:
                self.index = max(0, min(int(msg.get("index") or 0), len(items) - 1))
            except (TypeError, ValueError):
                self.index = 0
            self.query = " ".join(str(msg.get("query") or "").split())[:QUERY_MAX]
            style = msg.get("style") if isinstance(msg.get("style"), list) else []
            self.style = [" ".join(str(s).split())[:40] for s in style[:STYLE_MAX] if str(s).strip()]
            if self.pinned is not None and not any(self._same(i, self.pinned) for i in items):
                self.pinned = None  # new pictures replace a pinned one only when the server says so
                pane_store.clear_pin(self.store_dir)
            self.kind = "image"
            self.folded = False
            self._show_image(mirror=True)
            self._open(animate=True)
            self.coach.show("pane_open", self.image_view, self.lang, inset=_ImageView.CREDIT_H + 4)
        elif action == "index":
            if not self.items:
                return
            if "delta" in msg:
                self._nav(1 if int(msg.get("delta") or 0) > 0 else -1, emit=False)
            else:
                try:
                    self.index = max(0, min(int(msg.get("index") or 0), len(self.items) - 1))
                except (TypeError, ValueError):
                    return
                self.kind = "image"
                self._show_image(mirror=True)
                if not self.is_open:
                    self.folded = False
                    self._open(animate=True)
        elif action == "pin":
            if self.current_item() is not None and self.pinned is None:
                self.toggle_pin(emit=False)
        elif action == "unpin":
            if self.pinned is not None:
                self.toggle_pin(emit=False)
        elif action == "back":
            self.fold(emit=False)
        elif action == "show":
            self.unfold(emit=False)
        elif action == "close":
            self.close_pane(emit=False)
        else:
            return
        if spoken:
            self.pulse()
        self._emit_state()

    @staticmethod
    def _same(a: dict, b: dict) -> bool:
        return a.get("title") == b.get("title") and a.get("source_url") == b.get("source_url") \
            and len(a.get("image") or b"") == len(b.get("image") or b"")

    def _show_image(self, mirror: bool) -> None:
        item = self.current_item()
        if item is None:
            return
        L = self.lang
        image = self.images[self.index]
        self.image_view.set_item(item, image, L)
        if item["kind"] == "step" and item.get("steps"):
            caps = ui(L, "pane_step_caps", n=item.get("step") or self.index + 1, m=item["steps"])
        elif item["kind"] == "done":
            caps = ui(L, "pane_done_caps")
        else:
            caps = ui(L, "pane_idea_caps")
            if len(self.items) > 1:
                caps += " · " + ui(L, "pane_of", n=self.index + 1, m=len(self.items))
        if item.get("view"):
            caps += " · " + str(item["view"]).upper()  # one angle of a build with real shots (frente / lado / fundos)
        if self.pinned is not None and self._same(item, self.pinned):
            caps += " · " + ui(L, "pane_pinned_caps")
        self._set_caps(caps)
        _elide(self.title, item["title"])
        self.number.setText("")
        many = len(self.items) > 1
        self.btn_prev.setVisible(many)
        self.btn_next.setVisible(many)
        self.nav.setVisible(many)
        self.btn_prev.setEnabled(self.index > 0)
        self.btn_next.setEnabled(self.index < len(self.items) - 1)
        self.btn_pin.show()
        self._texts()
        self.pages.setCurrentWidget(self.image_view)
        self._place_content()
        key = (item["title"], item["source_url"], len(item["image"]))
        if mirror and key not in self._mirrored:
            self._mirrored.add(key)
            self.visual_shown.emit(pane_store.item_meta(item), image)

    def _nav(self, delta: int, emit: bool = True) -> None:
        if self.kind == "guide":
            self.guide_step.emit(delta)  # the keeper moves; follow_guide() brings it here
            return
        if not self.items:
            return
        target = self.index + delta
        if not 0 <= target < len(self.items):
            return
        self.index = target
        self._show_image(mirror=True)
        if emit:
            self._emit_state()

    def toggle_pin(self, emit: bool = True) -> None:
        """Pin the picture on screen (kept on this PC: the image and its
        source, client/gui/pane_store.py) or let it go."""
        item = self.current_item()
        if self.kind != "image" or item is None:
            return
        if self.pinned is not None:
            self.pinned = None
            pane_store.clear_pin(self.store_dir)
        else:
            self.pinned = item
            pane_store.save_pin(self.store_dir, item)
            self.coach.show("pane_pin", self.image_view, self.lang, inset=_ImageView.CREDIT_H + 4)
        self._show_image(mirror=False)
        if emit:
            self._emit_state()

    # ---- fold / unfold / close ----------------------------------------------
    def fold(self, emit: bool = True) -> None:
        """The arrow back to the orb: the glass goes back into the orb, the
        content stays (a click on the orb, or `show`, brings it back)."""
        if not self.is_open:
            return
        self.folded = True
        self.coach.hide_all()
        self._close(animate=True)
        if emit:
            self._emit_state()

    def unfold(self, emit: bool = True) -> None:
        if self.is_open or self.kind is None:
            return
        self.folded = False
        if self.kind == "guide" and self.guide_build is not None:
            self._show_guide_layer(animate=False)
        elif self.kind == "image" and self.items:
            self._show_image(mirror=False)
        else:
            return
        self._open(animate=True)
        if emit:
            self._emit_state()

    def orb_clicked(self) -> None:
        """A plain click on the in-game orb: a folded pane comes back."""
        if self.folded and not self.is_open:
            self.unfold()

    def close_pane(self, emit: bool = True) -> None:
        """The X (or "fecha"): a guide closes through its keeper (the window's
        pane closes too); pictures are forgotten, the pin with them."""
        if self.kind == "guide" and self.guide_build is not None:
            self.guide_close_requested.emit()  # follow_guide("") closes the glass
            return
        self.items, self.images, self.index, self.query, self.style = [], [], 0, "", []
        if self.pinned is not None:
            self.pinned = None
            pane_store.clear_pin(self.store_dir)
        self.coach.hide_all()
        self.folded = False
        self.kind = "guide" if self.guide_build is not None else None
        if self.kind == "guide":
            self._show_guide_layer(animate=False)
        else:
            self._close(animate=True)
        if emit:
            self._emit_state()

    def pulse(self) -> None:
        """A spoken command landed: a ripple from the small orb across the pane."""
        if self.phase == "open" and self.isVisible():
            self.ripple.start(QPointF(self.small_orb.mapTo(self.body, self.small_orb.rect().center())))

    # ---- geometry -------------------------------------------------------------
    def _screen_rect(self) -> QRect:
        screen = QApplication.screenAt(self.orb.orb_centre_global()) if self.orb is not None else None
        screen = screen or QApplication.primaryScreen()
        return screen.availableGeometry()

    def pane_rect(self) -> QRectF:
        return QRectF(MARGIN, MARGIN, self.width() - 2 * MARGIN, self.height() - 2 * MARGIN)

    def _slot_centre(self, pane_w: int, side: str) -> QPoint:
        """The small orb's centre inside the pane rect, for a `side`."""
        x = PAD + ORB_SLOT // 2 if side == "left" else pane_w - PAD - ORB_SLOT // 2
        return QPoint(x, PAD + HEADER_H // 2)

    def _place_window(self) -> None:
        """Around the orb: the pane hangs off the orb's spot (its small orb
        where the big one is, on the orb's side of the screen), kept inside
        the screen. The morph starts at the orb's real centre."""
        self._load_size()
        screen = self._screen_rect()
        anchor = self.orb.orb_centre_global()
        self._radius = float(self.orb.orb_radius())
        self.side = "right" if anchor.x() > screen.center().x() else "left"
        w, h = self._pane_size.width(), self._pane_size.height()
        slot = self._slot_centre(w, self.side)
        top_left = anchor - slot
        x = max(screen.left() + MARGIN, min(top_left.x(), screen.right() - w - MARGIN))
        y = max(screen.top() + MARGIN, min(top_left.y(), screen.bottom() - h - MARGIN))
        self.setGeometry(QRect(x - MARGIN, y - MARGIN, w + 2 * MARGIN, h + 2 * MARGIN))
        self._origin = QPointF(self.mapFromGlobal(anchor))
        self._apply_side()
        self._place_content()

    def _apply_side(self) -> None:
        self.row.setDirection(QHBoxLayout.Direction.RightToLeft if self.side == "right"
                              else QHBoxLayout.Direction.LeftToRight)
        align = Qt.AlignmentFlag.AlignRight if self.side == "right" else Qt.AlignmentFlag.AlignLeft
        self.caps.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)
        self.title.setAlignment(align | Qt.AlignmentFlag.AlignVCenter)

    def _set_caps(self, text: str) -> None:
        self._caps_text = text
        self._fit_caps()

    def _fit_caps(self) -> None:
        """The caps line at the width the header leaves (a tiny pane keeps
        the count: "…2 de 3")."""
        text = self._caps_text
        mode = Qt.TextElideMode.ElideLeft if "·" in text else Qt.TextElideMode.ElideRight
        self.caps.setText(self.caps.fontMetrics().elidedText(text, mode, max(16, self.caps.width() - 2)))

    def _place_content(self) -> None:
        pane = self.pane_rect().toRect()
        self.body.setGeometry(pane)
        inner = QRect(PAD, PAD, pane.width() - 2 * PAD, pane.height() - 2 * PAD)
        self.header.setGeometry(inner.x(), inner.y(), inner.width(), HEADER_H)
        self.row.activate()
        tw, th = self.titles.width(), self.titles.height()
        self.caps.setGeometry(0, 1, tw, 14)
        self.title.setGeometry(0, 15, tw, max(10, th - 15))
        self._fit_caps()
        _elide(self.title, self.guide_name if self.kind == "guide" else
               (self.current_item() or {}).get("title", ""))
        self.pages.setGeometry(inner.x(), inner.y() + HEADER_H + 4, inner.width(), max(10, inner.height() - HEADER_H - 4))
        self.ripple.setGeometry(self.body.rect())

    def slot_centre_global(self) -> QPoint:
        """Where the small orb is on screen: the orb comes back there after a close."""
        return self.small_orb.mapToGlobal(self.small_orb.rect().center())

    def grip_rect(self) -> QRect:
        """The resize corner: the bottom corner away from the orb."""
        pane = self.pane_rect().toRect()
        x = pane.right() - GRIP if self.side == "left" else pane.left()
        return QRect(x, pane.bottom() - GRIP, GRIP + 1, GRIP + 1)

    def _orb_moved(self) -> None:
        if self.active:
            return
        pane_store.save_layout(self.layout_path, orb=(self.orb.x(), self.orb.y()))

    # ---- the morph ----------------------------------------------------------------
    def _open(self, animate: bool) -> None:
        global _told_fullscreen
        if self.is_open:
            self._place_content()
            return
        if self.phase == "closing":
            self._timer.stop()
        self._place_window()
        self.body.hide()
        self.orb.hide()  # its body is drawn here, melting into the drop
        self._start("opening", animate)
        if not _told_fullscreen and fullscreen_exclusive():
            _told_fullscreen = True
            self.hidden_by_fullscreen.emit()

    def _close(self, animate: bool) -> None:
        if not self.is_open:
            return
        self._origin = QPointF(self.small_orb.mapTo(self, self.small_orb.rect().center()))
        self._start("closing", animate)

    def _start(self, phase: str, animate: bool) -> None:
        self.phase = phase
        self._t0 = time.monotonic()
        if not animate:
            self._finish()
            return
        self.show()
        self._timer.start(FRAME_MS)
        self._tick()

    def _finish(self) -> None:
        self._timer.stop()
        if self.phase == "opening":
            self.phase = "open"
            self._fade.setEnabled(False)
            self._fade.setOpacity(1.0)
            self.show()
            self._place_content()
            self.body.show()
            self.ripple.raise_()
            if not self._topmost.isActive():
                self._topmost.start(ov.TOPMOST_REASSERT_MS)
        elif self.phase == "closing":
            self.phase = "closed"
            centre = self.slot_centre_global()
            self.body.hide()
            self._topmost.stop()
            self.hide()
            self.orb.place_at_centre(centre)  # the orb comes back where the pane's small orb was
            self.orb.show()
            pane_store.save_layout(self.layout_path, orb=(self.orb.x(), self.orb.y()))
        self.update()

    def _tick(self) -> None:
        if self.phase not in ("opening", "closing"):
            self._timer.stop()
            return
        length = FADE_MS if _reduced() else (OPEN_MS if self.phase == "opening" else CLOSE_MS)
        ms = (time.monotonic() - self._t0) * 1000
        if ms >= length:
            self._finish()
            return
        a = content_opacity(self.phase, ms, _reduced())
        if a > 0.0:
            if not self.body.isVisible():
                self._place_content()
                self.body.show()
            self._fade.setEnabled(True)
            self._fade.setOpacity(a)
        else:
            self.body.hide()
        self.update()

    def shape_progress(self) -> tuple[float, float, float, float]:
        return morph_progress(self.phase, (time.monotonic() - self._t0) * 1000, _reduced())

    def paintEvent(self, event) -> None:  # noqa: N802
        if self.phase == "closed":
            return
        e, swell, wobble, opacity = self.shape_progress()
        t = time.monotonic() - self._clock
        pane = self.pane_rect()
        r = self._radius
        if e < 0.0:  # the breath at the end of a close
            scale = 1.0 - e * 2.5
            path = QPainterPath()
            path.addEllipse(self._origin, r * scale, r * scale)
            e = 0.0
        else:
            path = blob_path(self._origin, r, r, pane, RADIUS, e, wobble, t, swell)
        p = QPainter(self)
        p.setOpacity(opacity)
        bright = 0.55 + 0.35 * math.sin(math.pi * e) if e < 1.0 else 0.55
        paint_glass(p, path, bright, _mix(BODY, GLASS, e), s=GLOW_SCALE)
        if self._click_through and e >= 1.0:
            # the orb's click-through cue, on the glass: a thin ring of its own colour
            ring = QColor(state_color("falando"))
            ring.setAlphaF(0.45)
            p.setOpacity(opacity)
            p.setPen(QPen(ring, 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(pane.adjusted(-3, -3, 3, 3), RADIUS + 3, RADIUS + 3)
        face = face_opacity(self.phase, (time.monotonic() - self._t0) * 1000, _reduced())
        if face > 0.0 and indicator.custom():
            # the in-game orb's own disc, melting into the drop (or back): one shape, no cut
            box = r / 0.36
            side = int(box * 1.8)
            dpr = self.devicePixelRatioF()
            layer = QImage(int(side * dpr), int(side * dpr), QImage.Format.Format_ARGB32_Premultiplied)
            layer.setDevicePixelRatio(dpr)
            layer.fill(0)
            q = QPainter(layer)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            indicator.draw(q, QPointF(side / 2, side / 2), box, self.small_orb.state, t, calm=True)
            q.end()
            p.setOpacity(opacity * face)
            p.drawImage(QPointF(self._origin.x() - side / 2, self._origin.y() - side / 2), layer)
        if self.phase == "open" and not self._click_through:
            # the resize corner: three short strokes
            g = self.grip_rect()
            p.setOpacity(0.5)
            p.setPen(QPen(_rim(QRectF(g)), 1.2))
            flip = self.side == "right"
            for i in (5, 9, 13):
                if flip:
                    p.drawLine(QPointF(g.left() + 2, g.bottom() - i), QPointF(g.left() + i, g.bottom() - 2))
                else:
                    p.drawLine(QPointF(g.right() - i, g.bottom() - 2), QPointF(g.right() - 2, g.bottom() - i))
        p.end()

    # ---- native window behaviour (the orb's) ----------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._native_setup_done:
            self._native_setup_done = True
            ov.apply_corner_rounding(self)
            ov.apply_noactivate(self)
            ov.apply_capture_exclusion(self)
        if self._click_through:
            ov.set_native_click_through(self, True)

    def hideEvent(self, event) -> None:  # noqa: N802 - nothing moves while hidden
        self._timer.stop()
        self._topmost.stop()
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._place_content()
        super().resizeEvent(event)

    # ---- mouse: drag like the orb, resize from the corner --------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self.phase != "open":
            return
        pos = event.position().toPoint()
        mode = "resize" if self.grip_rect().contains(pos) else "move"
        self._drag = (mode, event.globalPosition().toPoint(), self.geometry())
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is None or not event.buttons() & Qt.MouseButton.LeftButton:
            if self.phase == "open":
                if self.grip_rect().contains(event.position().toPoint()):
                    self.setCursor(Qt.CursorShape.SizeFDiagCursor if self.side == "left" else Qt.CursorShape.SizeBDiagCursor)
                else:
                    self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        mode, start, geo = self._drag
        delta = event.globalPosition().toPoint() - start
        if mode == "move":
            self.move(geo.topLeft() + delta)
        else:
            self._resize_by(delta, geo)
        event.accept()

    def _resize_by(self, delta: QPoint, geo: QRect) -> None:
        screen = self._screen_rect().size()
        w0, h0 = geo.width() - 2 * MARGIN, geo.height() - 2 * MARGIN
        if self.side == "left":  # the grip is bottom-right: the top-left corner stays
            size = clamp_pane_size(w0 + delta.x(), h0 + delta.y(), screen)
            self.setGeometry(QRect(geo.topLeft(), QSize(size.width() + 2 * MARGIN, size.height() + 2 * MARGIN)))
        else:  # the grip is bottom-left: the top-right corner stays
            size = clamp_pane_size(w0 - delta.x(), h0 + delta.y(), screen)
            x = geo.right() + 1 - (size.width() + 2 * MARGIN)
            self.setGeometry(QRect(QPoint(x, geo.top()), QSize(size.width() + 2 * MARGIN, size.height() + 2 * MARGIN)))
        self._pane_size = size

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag is None:
            return
        mode = self._drag[0]
        self._drag = None
        if mode == "resize":
            pane_store.save_layout(self.layout_path, size=(self._pane_size.width(), self._pane_size.height()))
        self.orb.place_at_centre(self.slot_centre_global())  # the orb comes back where the pane is now
        pane_store.save_layout(self.layout_path, orb=(self.orb.x(), self.orb.y()))
        event.accept()
