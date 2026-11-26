"""client/gui/recipe_strip.py — the brewing strip: one potion recipe as a row
of icons (server/brewing.py; the owner's brief, docs/agents/arena_handoff.md
2026-11-22).

`[water bottle] › [nether wart] › [sugar]  =  ● Agilidade 3:00`
`   +[redstone] 8:00   +[glowstone] II 1:30`

The server sends the recipe as a `turn_cards` card of kind "recipe" right
after the question is known: `steps` (icon ids + the game's item names),
`result` (name, short name, colour, level, duration) and `modifiers` (what
redstone / glowstone / gunpowder / dragon's breath do to this potion). The
strip is built from that and nothing else - no text of its own beyond the
"=" and the "+".

Two homes for the same widget:
- `RecipeOverlay` on the Ombri window (client/gui/main_window.py): over the
  key tiles under the usage card (no button lives there), at most 360 px
  wide, the Build pane's glass; it fades in, stays SHOW_MS (~12 s) and fades
  out - with reduced motion it simply appears and goes. Never over the game.
- the Chat view, inside the answer's panel, where it stays (the recipe is
  part of the answer).

Icons are the owner's own screenshots served by GET /v1/brewing/icons/{id}.png
(main.py fetches and caches them, client/brewing_icons.py, and hands them
over as PNG bytes); a step whose icon isn't here yet shows the item's name
as a small text chip in the same place, and the icon takes the chip's place
when it arrives (`set_icons`). The result potion is text: its name and a dot
in the potion's colour, not an icon.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from client.gui import motion
from client.gui.build_pane import GLASS, _alpha, paint_glass
from client.gui.widgets import TEXT, TEXT_2, TEXT_3, _label

STRIP_MAX_W = 360     # the owner's cap: the smallest possible visual
ICON_PX = 36          # a step's icon (32-40 px in the brief)
MOD_ICON_PX = 18      # a modifier's icon on the second line
SHOW_MS = 12_000      # the overlay stays this long, then fades
FADE_IN_MS = 220
FADE_OUT_MS = 600
RADIUS = 14
CHIP_MAX_W = 96       # a text chip (missing icon) is elided past this

_ICONS: dict[str, QPixmap] = {}  # icon id -> pixmap, for every strip in this process


def set_icons(icons: dict) -> list[str]:
    """PNG bytes (or base64 text) per icon id from main.py; returns the ids
    that decoded. Bad bytes are ignored - the chip stays."""
    import base64

    added = []
    for icon_id, data in (icons or {}).items():
        if not isinstance(icon_id, str) or not icon_id:
            continue
        raw = data
        if isinstance(data, str):
            try:
                raw = base64.b64decode(data)
            except (ValueError, TypeError):
                continue
        if not isinstance(raw, (bytes, bytearray)) or not raw.startswith(b"\x89PNG"):
            continue
        pm = QPixmap()
        if pm.loadFromData(bytes(raw), "PNG") and not pm.isNull():
            _ICONS[icon_id] = pm
            added.append(icon_id)
    return added


def icon_pixmap(icon_id: str) -> QPixmap | None:
    return _ICONS.get(str(icon_id or ""))


def has_icon(icon_id: str) -> bool:
    return str(icon_id or "") in _ICONS


def clear_icons() -> None:  # tests
    _ICONS.clear()


def clean_recipe(raw) -> dict | None:
    """The card as the strip uses it, or None when it isn't a recipe card
    (not a dict, not kind "recipe", no steps or no result)."""
    if not isinstance(raw, dict) or str(raw.get("kind") or "") != "recipe":
        return None
    steps = []
    for s in raw.get("steps") or []:
        if not isinstance(s, dict):
            continue
        icon = str(s.get("icon") or "").strip()
        label = " ".join(str(s.get("label") or "").split())
        if icon or label:
            steps.append({"icon": icon, "label": label or icon})
    result = raw.get("result") if isinstance(raw.get("result"), dict) else None
    if not steps or result is None:
        return None
    color = str(result.get("color") or "").strip()
    if not QColor(color).isValid():
        color = TEXT_2
    mods = []
    for m in raw.get("modifiers") or []:
        if isinstance(m, dict) and (m.get("icon") or m.get("label")):
            mods.append({"icon": str(m.get("icon") or ""), "label": " ".join(str(m.get("label") or "").split()),
                         "text": " ".join(str(m.get("text") or "").split())})
    return {
        "kind": "recipe",
        "steps": steps,
        "result": {"name": " ".join(str(result.get("name") or "").split()),
                   "short": " ".join(str(result.get("short") or result.get("name") or "").split()),
                   "color": color, "duration": " ".join(str(result.get("duration") or "").split()),
                   "instant": bool(result.get("instant"))},
        "modifiers": mods,
        "caption": " ".join(str(raw.get("caption") or "").split()),
        "potion": str(raw.get("potion") or ""),
    }


# ---------------------------------------------------------------------- pieces
class _Step(QWidget):
    """One step: the icon, or the item's name as a text chip until the icon
    is here. Hovering names the item either way."""

    def __init__(self, icon_id: str, label: str, px: int = ICON_PX, chip_max: int = CHIP_MAX_W) -> None:
        super().__init__()
        self.icon_id = icon_id
        self.label_text = label
        self.px = px
        self.chip_max = chip_max  # the chip's text is elided past this (hover names it whole)
        self.pm: QPixmap | None = None
        self.setToolTip(label)
        self._chip_w = 0
        self._apply(icon_pixmap(icon_id))

    def _apply(self, pm: QPixmap | None) -> None:
        self.pm = pm
        if pm is not None:
            self.setFixedSize(self.px, self.px)
        else:
            font = self.font()
            font.setPixelSize(11)
            text_w = min(self.chip_max, QFontMetrics(font).horizontalAdvance(self.label_text) + 2)
            self._chip_w = max(28, text_w + 14)
            self.setFixedSize(self._chip_w, self.px)
        self.update()

    def refresh(self) -> bool:
        """Takes the icon when it is in the cache; True when it changed."""
        pm = icon_pixmap(self.icon_id)
        if pm is self.pm:
            return False
        self._apply(pm)
        return True

    @property
    def is_chip(self) -> bool:
        return self.pm is None

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.pm is not None:
            scaled = self.pm.scaled(self.px, self.px, Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            p.drawPixmap(x, y, scaled)
        else:
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_alpha("#FFFFFF", 0.06))
            p.drawRoundedRect(rect, 9, 9)
            p.setPen(QColor(TEXT_2))
            font = self.font()
            font.setPixelSize(11)
            p.setFont(font)
            fm = QFontMetrics(font)
            text = fm.elidedText(self.label_text, Qt.TextElideMode.ElideRight, self.width() - 10)
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        p.end()


class _Arrow(QLabel):
    def __init__(self, text: str = "›") -> None:
        super().__init__(text)
        self.setStyleSheet(f"font-size: 15px; color: {TEXT_3}; background: transparent;")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedWidth(12)


class _Dot(QWidget):
    def __init__(self, color: str) -> None:
        super().__init__()
        self._color = QColor(color)
        self.setFixedSize(12, 12)
        self.setToolTip("")

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_alpha(self._color, 0.35))
        p.drawEllipse(QRectF(0, 0, 12, 12))
        p.setBrush(self._color)
        p.drawEllipse(QRectF(2.5, 2.5, 7, 7))
        p.end()


# ---------------------------------------------------------------------- the strip
class RecipeStrip(QWidget):
    """The strip itself: the chain, the result, the modifiers line. Glass
    behind it when `glass` (the overlay); plain inside the chat's panel.
    A long chain (a lingering corruption: six steps) first gets smaller
    icons, then the result moves to the second line; only then is the name
    elided - never wider than `max_w`."""

    # (icon px, result on the 2nd line, a text chip's widest text, one modifier per line).
    # The last ones only matter while icons are chips (not downloaded yet, or a failed
    # fetch): before 2026-11-25 nothing narrowed a chip, the strip was pinned to `max_w`
    # anyway and Qt slid the fixed-width chips over their neighbours ("Pó de Redstone"
    # under "8:00" in the chat, 8 of 10 potions with no icons yet).
    # A 6-step chain of chips still did not fit the overlay's 360 px: the chain itself then
    # wraps onto two lines (the 5th field), chips kept readable.
    LAYOUTS = ((ICON_PX, False, CHIP_MAX_W, False, False), (32, False, CHIP_MAX_W, False, False),
               (32, True, CHIP_MAX_W, False, False), (28, True, CHIP_MAX_W, False, False),
               (28, True, 72, False, False), (28, True, 56, False, False), (28, True, 56, True, False),
               (28, True, 72, True, True), (28, True, 56, True, True), (28, True, 44, True, True),
               (28, True, 34, True, True))
    # With a height limit too (`max_h`: the overlay, whose layer is the key tiles' row
    # +-8 px and clips its child): every extra line costs ~22 px, so narrower chips with
    # the modifiers kept on one line come before any split (2026-11-26, Arena: with no
    # icons yet the overlay strips were 116-147 px tall in an 89 px layer, the last
    # modifier row cut in half). The chat has no height limit and keeps LAYOUTS.
    # A wrapped chain (six steps) ends its second line with the result: two lines, not three.
    SHORT_LAYOUTS = (LAYOUTS[:6] + ((28, True, 44, False, False), (28, True, 34, False, False)) + LAYOUTS[6:]
                     + ((28, False, 56, False, True), (28, False, 44, False, True), (28, False, 34, False, True)))

    def __init__(self, card: dict, glass: bool = True, max_w: int = STRIP_MAX_W, max_h: int | None = None) -> None:
        super().__init__()
        self.card = card
        self.glass = glass
        self.max_w = max_w
        self.max_h = max_h if max_h and max_h > 0 else None
        self.mods_shown = True  # False: the overlay had no room for the modifiers' row (the chat keeps them)
        self.steps: list[_Step] = []
        self.mod_steps: list[_Step] = []
        self.icon_px = ICON_PX
        self.result_below = False
        self.chip_max = CHIP_MAX_W
        self.mods_split = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._col = QVBoxLayout(self)
        pad = 12 if glass else 4
        self._col.setContentsMargins(pad + 2, pad - 2, pad + 2, pad - 3)
        self._col.setSpacing(4)
        self._fit()

    # ------------------------------------------------------------ building
    def _clear(self) -> None:
        def drop(layout) -> None:
            while layout.count():
                item = layout.takeAt(0)
                if item.widget() is not None:
                    item.widget().setParent(None)
                    item.widget().deleteLater()
                elif item.layout() is not None:
                    drop(item.layout())
                    item.layout().deleteLater()
        drop(self._col)
        self.steps = []
        self.mod_steps = []

    def _small(self, text: str, color: str = TEXT_2, tip: str = "") -> QLabel:
        lab = QLabel(text)
        lab.setStyleSheet(f"font-size: 12px; color: {color}; background: transparent;")
        if tip:
            lab.setToolTip(tip)
        return lab

    def _result_widgets(self) -> list[QWidget]:
        r = self.card["result"]
        self.name = _label(r["short"], size=13, weight=600, color=TEXT)
        self.name.setToolTip(r["name"])
        self.name.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {TEXT}; background: transparent;")
        out: list[QWidget] = [_Arrow("="), _Dot(r["color"]), self.name]
        self.dur = self._small(r["duration"]) if r["duration"] else None
        if self.dur is not None:
            out.append(self.dur)
        return out

    def _build(self, icon_px: int, result_below: bool, chip_max: int = CHIP_MAX_W, mods_split: bool = False,
               chain_split: bool = False) -> None:
        self._clear()
        self.icon_px = icon_px
        self.result_below = result_below
        self.chip_max = chip_max
        self.mods_split = mods_split
        self.mod_texts: list[QLabel] = []
        card = self.card
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4 if icon_px >= 32 else 2)
        first_line = (len(card["steps"]) + 1) // 2 if chain_split else len(card["steps"])
        for i, s in enumerate(card["steps"]):
            if i == first_line:
                # the chain's second line starts with its arrow, so it reads as one chain
                row.addStretch(1)
                self._col.addLayout(row)
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(4 if icon_px >= 32 else 2)
            if i:
                row.addWidget(_Arrow(), 0, Qt.AlignmentFlag.AlignVCenter)
            step = _Step(s["icon"], s["label"], px=icon_px, chip_max=chip_max)
            self.steps.append(step)
            row.addWidget(step, 0, Qt.AlignmentFlag.AlignVCenter)
        result = self._result_widgets()
        if not result_below:
            row.addSpacing(4)
            for wdg in result:
                row.addWidget(wdg, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        self._col.addLayout(row)
        # the modifiers that change the potion itself (the chain already shows one that was asked)
        in_chain = {s["icon"] for s in card["steps"]}
        mods = [m for m in card["modifiers"] if m["icon"] in ("redstone", "glowstone_dust") and m["icon"] not in in_chain]
        if not self.mods_shown:
            mods = []
        if result_below or mods:
            mrow = QHBoxLayout()
            mrow.setContentsMargins(0, 0, 0, 0)
            mrow.setSpacing(4)
            if result_below:
                for wdg in result:
                    mrow.addWidget(wdg, 0, Qt.AlignmentFlag.AlignVCenter)
                if mods and not mods_split:
                    mrow.addSpacing(6)
                    mrow.addWidget(self._small("·", TEXT_3), 0, Qt.AlignmentFlag.AlignVCenter)
            for i, m in enumerate(mods):
                if mods_split and (i or result_below):
                    # one modifier per line: the row so far is closed and a new one starts
                    mrow.addStretch(1)
                    self._col.addLayout(mrow)
                    mrow = QHBoxLayout()
                    mrow.setContentsMargins(0, 0, 0, 0)
                    mrow.setSpacing(4)
                mrow.addWidget(self._small("+", TEXT_3), 0, Qt.AlignmentFlag.AlignVCenter)
                step = _Step(m["icon"], m["label"], px=MOD_ICON_PX, chip_max=chip_max)
                self.mod_steps.append(step)
                mrow.addWidget(step, 0, Qt.AlignmentFlag.AlignVCenter)
                text = self._small(m["text"], tip=m["label"])
                self.mod_texts.append(text)
                mrow.addWidget(text, 0, Qt.AlignmentFlag.AlignVCenter)
                mrow.addSpacing(8)
            mrow.addStretch(1)
            self._col.addLayout(mrow)
        self.setToolTip(card["caption"])
        if self.isVisible():
            # Qt shows a visible widget's new layout children on the next event-loop
            # pass only; measured before that they count for nothing. Show them now.
            for child in self.findChildren(QWidget):
                child.show()

    def _fit(self) -> None:
        """The first layout that fits `max_w` (and `max_h`, when there is one);
        the name is elided as a last resort. When nothing fits the height, the
        shortest layout that fits the width."""
        layouts = self.SHORT_LAYOUTS if self.max_h else self.LAYOUTS
        # With a height limit and nothing that fits it (all icons still text chips, a long
        # chain), the overlay leaves the modifiers out rather than be clipped: the chat's
        # strip keeps them, and `recipe_in_chat` says the recipe stays there.
        fallback = None  # (height, mods shown, layout): the shortest that fits the width
        for mods_shown in ((True, False) if self.max_h else (True,)):
            self.mods_shown = mods_shown
            for layout in layouts:
                self._build(*layout)
                self.adjustSize()
                hint = self.sizeHint()
                if hint.width() > self.max_w:
                    continue
                if self.max_h is None or hint.height() <= self.max_h:
                    fallback = None
                    break
                if fallback is None or hint.height() < fallback[0]:
                    fallback = (hint.height(), mods_shown, layout)
            else:
                continue
            break
        if fallback is not None:
            self.mods_shown = fallback[1]
            self._build(*fallback[2])
            self.adjustSize()
        hint = self.sizeHint()
        if hint.width() > self.max_w:
            over = hint.width() - self.max_w
            fm = QFontMetrics(self.name.font())
            full = fm.horizontalAdvance(self.card["result"]["short"])
            width = max(40, full - over)
            self.name.setMaximumWidth(width)
            self.name.setText(fm.elidedText(self.card["result"]["short"], Qt.TextElideMode.ElideRight, width))
            self.adjustSize()
            hint = self.sizeHint()
        self.setFixedSize(min(hint.width(), self.max_w), hint.height())

    # ------------------------------------------------------------ icons
    def refresh_icons(self) -> int:
        """Chips become icons as they arrive; returns how many changed."""
        changed = sum(1 for s in self.steps + self.mod_steps if s.refresh())
        if changed:
            self._fit()
        return changed

    def missing_icons(self) -> list[str]:
        return [s.icon_id for s in self.steps + self.mod_steps if s.is_chip and s.icon_id]

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self.glass:
            return
        p = QPainter(self)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(3, 3, -3, -3), RADIUS, RADIUS)
        paint_glass(p, path, 0.55, QColor(GLASS), 1.0, 70.0)
        p.end()


# ---------------------------------------------------------------------- the overlay
class RecipeOverlay(QWidget):
    """The strip's place on the Ombri window: a transparent layer the size
    of the key tiles' row, the strip centred in it. `show_recipe` replaces
    whatever was showing and restarts the clock; after SHOW_MS the strip
    fades and the layer hides. Mouse events pass through - nothing under it
    is clickable anyway, but a stray click must never be swallowed."""

    changed = Signal()  # shown or gone: the window re-reads `active`

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.strip: RecipeStrip | None = None
        self.card: dict | None = None
        self.shown_at = 0.0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self._fade = None
        self._effect: QGraphicsOpacityEffect | None = None
        self.hide()

    @property
    def active(self) -> bool:
        return self.strip is not None

    def show_recipe(self, card: dict, show_ms: int = SHOW_MS) -> None:
        self._drop()
        self.card = card
        # the layer clips its child: the strip is told how tall it may be (None before any geometry)
        self.strip = RecipeStrip(card, glass=True, max_h=self.height() if self.height() > 40 else None)
        self.strip.setParent(self)
        self._place()
        self.strip.show()
        self.shown_at = time.monotonic()
        self.show()
        self.raise_()
        if motion.enabled():
            self._effect = QGraphicsOpacityEffect(self.strip)
            self._effect.setOpacity(0.0)
            self.strip.setGraphicsEffect(self._effect)
            self._fade = motion.tween(self, 0.0, 1.0, FADE_IN_MS, self._set_opacity, self._clear_effect)
        self._timer.start(max(0, int(show_ms)))
        self.changed.emit()

    def dismiss(self) -> None:
        """The clock ran out (or the view changed for good): fade and hide."""
        if self.strip is None:
            return
        self._timer.stop()
        strip = self.strip
        if motion.enabled() and self.isVisible():
            self._effect = QGraphicsOpacityEffect(strip)
            strip.setGraphicsEffect(self._effect)
            self._fade = motion.tween(self, 1.0, 0.0, FADE_OUT_MS, self._set_opacity, self._drop)
        else:
            self._drop()

    def refresh_icons(self) -> None:
        if self.strip is not None and self.strip.refresh_icons():
            self._place()

    def resizeEvent(self, event) -> None:  # noqa: N802
        if self.strip is not None and self.height() > 40 and self.strip.max_h != self.height():
            self.strip.max_h = self.height()  # a new layer height: the strip picks its layout again
            self.strip._fit()
        self._place()
        super().resizeEvent(event)

    # ------------------------------------------------------------ internals
    def _place(self) -> None:
        if self.strip is None:
            return
        self.strip.move((self.width() - self.strip.width()) // 2, (self.height() - self.strip.height()) // 2)

    def _set_opacity(self, v: float) -> None:
        if self._effect is not None and self.strip is not None and self.strip.graphicsEffect() is self._effect:
            self._effect.setOpacity(float(v))

    def _clear_effect(self) -> None:
        if self.strip is not None and self._effect is not None and self.strip.graphicsEffect() is self._effect:
            self.strip.setGraphicsEffect(None)
        self._effect = None

    def _drop(self) -> None:
        self._timer.stop()
        if self._fade is not None:
            try:
                self._fade.stop()
            except RuntimeError:
                pass
            self._fade = None
        self._effect = None
        if self.strip is not None:
            self.strip.hide()
            self.strip.setParent(None)
            self.strip.deleteLater()
            self.strip = None
        self.card = None
        self.hide()
        self.changed.emit()
