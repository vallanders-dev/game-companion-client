"""client/gui/chat_view.py — the chat beside the home column of the main window
(docs/chat_redesign.md, 2026-11-23; phase 1 of docs/agentic_chat.md before it).

The window widens to the right and this panel opens next to the unchanged
home column (the mascot never moves). Two tabs: **Conversa** (this run's
turns) and **Links** (every link Ombri sent, kept on this PC by
client/links_store.py - this module itself writes nothing).

Look (owner, 2026-11-23): solid and dark, no gradients. Panels are near-black
with a 1 px light hairline and a soft shadow. The player's messages are flat
rows; Ombri's are panels. Only the newest Ombri panel moves: a faint,
diffused magenta glow (client/gui/motion.py GLOW_*), painted by the list host
behind it so it can spread past the panel's edge.

A turn's life:
  1. the player types and sends (`send_requested`) -> the question row and a
     thinking row appear at once; or F8: "ouvindo" opens a pending row whose
     question fills in when `turn_meta` arrives (`add_question`).
  2. the thinking row: the mini mascot in its thinking pose with a circling
     arc, a shimmering stage label that only says what is really happening
     (screen / pensando / pesquisando, the server's own turn states) and three
     step dots.
  3. `append_answer` pieces stream in, each fading in; then cards
     (`set_cards`), one after another.

Memory only for the turns: CLAUDE.md, nothing on disk links a question to a
player. Cards open only on a left click, only http(s), through `open_url()`
(module-level so a test can replace it).

Three contextual hints (client/gui/hints.py, the app-wide pass of
2026-11-25) along the bottom of the conversation, over the last panel and
above the input: `chat_links` when the first link card lands, `chat_repeat`
when a spoken answer ends, `note_recall` when a note is kept. `coach` is the
window's shared Hints (one hints.json); on its own the view counts in memory.
"""
from __future__ import annotations

import html
import time
import webbrowser
from datetime import date, datetime
from urllib.parse import urlsplit

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

from client.gui import hints as hints_mod, indicator, motion
from client.gui.fonts import BODY_FAMILY, HEADING_FAMILY
from client.gui.ui_texts import ui
from client.gui.widgets import ACCENT, CONTROL, OK_GREEN, TEXT, TEXT_2, TEXT_3, _label, line_icon

MAX_TURNS = 200
MAX_CARDS_PER_TURN = 6
CARD_COLUMNS = 1
TITLE_MAX = 90
CARD_KINDS = ("video", "link", "image")
MESSAGE_MAX = 500        # characters a typed message may have (docs/chat_redesign.md)
CHAT_W = 460             # the panel's width beside the 440 px home column

CHAT_BG = "#0E0F13"
PANEL = "#16181E"
PANEL_HOVER = "#1A1D24"
HAIR = QColor(255, 255, 255, 18)
HAIR_HOVER = QColor(255, 255, 255, 34)
MAGENTA = "#E25DD0"
PANEL_RADIUS = 12
SHADOW_PAD = 6           # room around a panel for its own soft shadow

STAGES = ("listening", "screen", "pensando", "pesquisando")


def open_url(url: str) -> bool:
    """A card's click: the player's default browser. Module-level on purpose -
    eval/client_ui_harness.py replaces it and proves nothing opens by itself."""
    return webbrowser.open(url)


def clean_card(raw) -> dict | None:
    """The card as rendered, or None when it must not be: not a dict, no title,
    or a URL that isn't plain http(s) (nothing else ever reaches the browser)."""
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url") or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or any(ch.isspace() for ch in url):
        return None
    title = " ".join(str(raw.get("title") or "").split())
    if not title:
        return None
    kind = str(raw.get("kind") or "link").strip().lower()
    site = " ".join(str(raw.get("site") or "").split()) or parts.netloc.removeprefix("www.")
    thumb = str(raw.get("thumb") or "").strip()
    return {"kind": kind if kind in CARD_KINDS else "link", "url": url, "title": title, "site": site,
            "thumb": thumb if urlsplit(thumb).scheme in ("http", "https") else ""}


def visual_card(item: dict) -> dict:
    """The chat's card for a picture the in-game pane showed (client/gui/game_pane.py
    `visual_shown`): kind "image", the title, the site and creator as the
    meta line, the source page as the URL when it is http(s) - an item with
    no source page is a card that opens nothing (the thumbnail is the point)."""
    url = str(item.get("source_url") or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        url = ""
    site = " ".join(str(item.get("site") or "").split())
    author = " ".join(str(item.get("author") or "").split())
    meta = " · ".join(x for x in (site, author) if x)
    title = " ".join(str(item.get("title") or "").split()) or "…"
    view = " ".join(str(item.get("view") or "").split())  # one angle of a build with real shots: "casa inicial · frente"
    if view:
        title += f" · {view}"
    return {"kind": "image", "url": url, "title": title, "site": meta, "thumb": ""}


# ---------------------------------------------------------------------- painting
def paint_panel(p: QPainter, rect: QRectF, *, fill: str = PANEL, hair: QColor = HAIR,
                radius: float = PANEL_RADIUS, shadow: float = 1.0) -> None:
    """The panel look: a soft dark drop shadow (a few widening rings, cheap),
    the near-black fill, the light hairline."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if shadow > 0:
        for i in range(1, SHADOW_PAD + 1):
            a = int(46 * shadow * (1 - i / (SHADOW_PAD + 1)) ** 2)
            p.setPen(QPen(QColor(0, 0, 0, a), 1.0))
            p.drawRoundedRect(rect.adjusted(-i + 0.5, -i + 2.5, i - 0.5, i + 1.5), radius + i, radius + i)
    p.setPen(QPen(hair, 1.0))
    p.setBrush(QColor(fill))
    p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


def paint_glow(p: QPainter, rect: QRectF, level: float, radius: float = PANEL_RADIUS) -> None:
    """The diffused magenta glow around `rect`: rings fading outwards with a
    squared falloff, so there is no visible edge (owner: less conspicuous)."""
    if level <= 0.001:
        return
    r, g, b = motion.GLOW_RGB
    spread = motion.GLOW_SPREAD
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for i in range(1, spread + 1, 2):
        a = int(255 * level * (1 - i / (spread + 1)) ** 2)
        if a <= 0:
            continue
        p.setPen(QPen(QColor(r, g, b, a), 2.0))
        p.drawRoundedRect(rect.adjusted(-i, -i, i, i), radius + i, radius + i)


class Panel(QFrame):
    """A frame painted as a panel. `hover` lifts it 1 px and brightens the
    hairline (animated); used by Ombri's rows, cards, link rows, the input."""

    def __init__(self, hover: bool = False, fill: str = PANEL, radius: int = PANEL_RADIUS) -> None:
        super().__init__()
        self._hover_enabled = hover
        self._fill = fill
        self._radius = radius
        self._lift = 0.0  # 0..1
        self._hair = QColor(HAIR)
        self._press = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, hover)
        self.setContentsMargins(SHADOW_PAD, SHADOW_PAD - 2, SHADOW_PAD, SHADOW_PAD + 2)

    def set_hair(self, color: QColor) -> None:
        self._hair = QColor(color)
        self.update()

    def _animate_lift(self, to: float) -> None:
        start = self._lift

        def step(v: float) -> None:
            self._lift = v
            self.update()
        motion.tween(self, start, to, motion.FAST, step)

    def enterEvent(self, event) -> None:  # noqa: N802
        if self._hover_enabled:
            self._animate_lift(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover_enabled:
            self._animate_lift(0.0)
            self._press = 0.0
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._hover_enabled and event.button() == Qt.MouseButton.LeftButton:
            self._press = 1.0
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._press:
            self._press = 0.0
            self.update()
        super().mouseReleaseEvent(event)

    def panel_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(SHADOW_PAD, SHADOW_PAD - 2, -SHADOW_PAD, -SHADOW_PAD - 2)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        rect = self.panel_rect().translated(0, -1.0 * self._lift)
        if self._press:
            k = 0.01  # the press: 98-99 % of the size, around the centre
            rect = rect.adjusted(rect.width() * k, rect.height() * k, -rect.width() * k, -rect.height() * k)
        hair = QColor(self._hair)
        if self._lift:
            hair.setAlpha(int(hair.alpha() + (HAIR_HOVER.alpha() - HAIR.alpha()) * self._lift))
        fill = QColor(self._fill)
        if self._lift:
            hov = QColor(PANEL_HOVER)
            fill = QColor(int(fill.red() + (hov.red() - fill.red()) * self._lift),
                          int(fill.green() + (hov.green() - fill.green()) * self._lift),
                          int(fill.blue() + (hov.blue() - fill.blue()) * self._lift))
        paint_panel(p, rect, fill=fill.name(), hair=hair, radius=self._radius, shadow=1.0 + 0.4 * self._lift)
        p.end()


# ---------------------------------------------------------------------- small animated pieces
class MiniOmbri(QWidget):
    """The in-game orb (client/gui/overlay.py's look: indicator.draw with
    calm=True on its dark disc) at row size - owner, 2026-11-23: the orb, not
    the home column's mascot. While `thinking` it takes the thinking pose and a
    short arc in the palette circles it; at rest it holds still. Its timer runs
    only while it thinks and the window is on screen (motion.quiet)."""

    BACKING = QColor(20, 22, 28, 204)  # the overlay's disc: DARK_THEME.surface_fill

    def __init__(self, size: int = 30) -> None:
        super().__init__()
        self.setFixedSize(size, size)
        self.thinking = False
        self._t0 = time.monotonic()
        self._arc = 0.0  # 0..1, eased in and out
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

    def set_thinking(self, on: bool) -> None:
        self.thinking = on
        if on and not self._timer.isActive():
            self._timer.start(16)
        self.update()

    def _tick(self) -> None:
        target = 1.0 if self.thinking else 0.0
        self._arc += (target - self._arc) * 0.12
        if not self.thinking and self._arc < 0.02:
            self._arc = 0.0
            self._timer.stop()
        if motion.quiet(self):
            return
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = time.monotonic() - self._t0
        c = QPointF(self.width() / 2, self.height() / 2)
        state = "pensando" if self.thinking else "idle"
        if indicator.custom():
            # a little inside the widget, so the thinking arc circles the disc
            indicator.draw(p, c, self.width() - 7, state, t, calm=True, backing=self.BACKING)
        if self._arc > 0.01:
            r = self.width() / 2 - 1.5
            grad = QLinearGradient(QPointF(0, 0), QPointF(self.width(), self.height()))
            for stop, col in ((0.0, OK_GREEN), (0.55, MAGENTA), (1.0, ACCENT)):
                q = QColor(col)
                q.setAlphaF(0.9 * self._arc)
                grad.setColorAt(stop, q)
            pen = QPen(QBrush(grad), 1.8)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            start = -(t * 360 / 1.6) % 360
            p.drawArc(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), int(start * 16), int(80 * 16))
        p.end()


class ShimmerLabel(QWidget):
    """A one-line label whose text a soft light sweeps across (the stage of a
    turn: "Pensando…"). `shimmer=False` paints it still (the time, later)."""

    def __init__(self) -> None:
        super().__init__()
        self.text = ""
        self.shimmer = False
        self.color = QColor(TEXT_3)
        self._t0 = time.monotonic()
        self._font = QFont(BODY_FAMILY)
        self._font.setPixelSize(12)
        self.setFixedHeight(16)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._fade = 1.0

    def set_text(self, text: str, shimmer: bool) -> None:
        if text != self.text:
            self._fade = 0.0
            motion.tween(self, 0.0, 1.0, motion.SMALL, self._set_fade)
        self.text = text
        self.shimmer = shimmer
        if shimmer and not self._timer.isActive():
            self._timer.start(16)
        elif not shimmer:
            self._timer.stop()
        self.setMinimumWidth(QFontMetrics(self._font).horizontalAdvance(text) + 4)
        self.update()

    def _set_fade(self, v: float) -> None:
        self._fade = v
        self.update()

    def _tick(self) -> None:
        if not motion.quiet(self):
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setFont(self._font)
        p.setOpacity(self._fade)
        if self.shimmer and motion.enabled():
            w = max(1, self.width())
            x = ((time.monotonic() - self._t0) / 1.8 % 1.0) * (w * 2.2) - w * 0.6
            grad = QLinearGradient(QPointF(x - 40, 0), QPointF(x + 40, 0))
            grad.setColorAt(0.0, QColor(TEXT_3))
            grad.setColorAt(0.5, QColor("#F2F3F5"))
            grad.setColorAt(1.0, QColor(TEXT_3))
            p.setPen(QPen(QBrush(grad), 1))
        else:
            p.setPen(self.color)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text)
        p.end()


class StepDots(QWidget):
    """Three dots for a turn's stages: the current one magenta, done ones blue."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(30, 10)
        self.step = 0
        self.done = False
        self.hidden_away = False

    def set_step(self, step: int, done: bool = False) -> None:
        self.step, self.done = step, done
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            if self.done or i < self.step:
                col = QColor(OK_GREEN)
            elif i == self.step:
                col = QColor(MAGENTA)
            else:
                col = QColor("#3A3F4B")
            p.setBrush(col)
            p.drawEllipse(QPointF(4 + i * 10, 5), 3, 3)
        p.end()


class IconButton(QPushButton):
    """A small icon button with an animated hover (the icon brightens) and a
    check-morph for "Copiar" (`flash_check`)."""

    def __init__(self, icon: str, tip: str = "", size: int = 26, icon_size: int = 15) -> None:
        super().__init__()
        self._icon_name = icon
        self._icon_size = icon_size
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tip)
        self.setObjectName("iconbtn")
        self._set_icon(TEXT_3)

    def _set_icon(self, color: str, name: str | None = None) -> None:
        self.setIcon(line_icon(name or self._icon_name, color, self._icon_size))
        self.setIconSize(QSize(self._icon_size, self._icon_size))

    def enterEvent(self, event) -> None:  # noqa: N802
        self._set_icon(TEXT)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._set_icon(TEXT_3)
        super().leaveEvent(event)

    def flash_check(self) -> None:
        self._set_icon(OK_GREEN, "check")
        QTimer.singleShot(1200, self, lambda: self._set_icon(TEXT_3))


# ---------------------------------------------------------------------- cards
class ChatCard(Panel):
    """One card under an answer: the kind's icon, the title and the site. A
    left click opens the URL in the browser; nothing happens before that."""

    def __init__(self, card: dict, lang: str, thumb: QImage | None = None) -> None:
        super().__init__(hover=bool(card["url"]), fill="#111318", radius=10)
        self.setObjectName("chatlink")  # painted by Panel; no style-sheet frame
        self.card = card
        self.url: str = card["url"]
        self.kind: str = card["kind"]
        if self.url:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip(f"{ui(lang, 'chat_card_open')}\n{self.url}")
        else:
            self.setToolTip(card["title"])
        self.setFixedHeight(64 + SHADOW_PAD * 2)
        row = QHBoxLayout(self)
        row.setContentsMargins(SHADOW_PAD + 10, SHADOW_PAD + 6, SHADOW_PAD + 12, SHADOW_PAD + 8)
        row.setSpacing(12)
        self.thumb = QFrame()
        self.thumb.setObjectName("thumb")
        self.thumb.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.thumb.setFixedSize(40, 40)
        icon = QLabel(self.thumb)
        if thumb is not None and not thumb.isNull():
            # the picture itself (the in-game pane's mirror), cropped to the square, rounded
            side = min(thumb.width(), thumb.height())
            square = thumb.copy((thumb.width() - side) // 2, (thumb.height() - side) // 2, side, side)
            pm = QPixmap.fromImage(square.scaled(80, 80, Qt.AspectRatioMode.IgnoreAspectRatio,
                                                 Qt.TransformationMode.SmoothTransformation))
            rounded = QPixmap(80, 80)
            rounded.fill(Qt.GlobalColor.transparent)
            q = QPainter(rounded)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, 80, 80), 16, 16)
            q.setClipPath(path)
            q.drawPixmap(0, 0, pm)
            q.end()
            rounded.setDevicePixelRatio(2)
            icon.setPixmap(rounded)
            icon.setGeometry(0, 0, 40, 40)
            self.thumb.setStyleSheet("background: transparent;")
        else:
            color = ACCENT if self.kind == "video" else OK_GREEN
            icon.setPixmap(line_icon("play" if self.kind == "video" else self.kind, color, 20).pixmap(20, 20))
            icon.setGeometry(10, 10, 20, 20)
        row.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addStretch(1)
        title = card["title"]
        if len(title) > TITLE_MAX:
            title = title[:TITLE_MAX - 1].rstrip() + "…"
        self.title = _label(title, size=13, weight=600)
        self.title.setWordWrap(True)
        self.title.setMaximumHeight(36)
        col.addWidget(self.title)
        meta = ui(lang, f"chat_kind_{self.kind}")
        if card["site"]:
            meta = f"{card['site']} · {meta}"
        self.site = _label(meta, size=11, color=TEXT_3)
        col.addWidget(self.site)
        col.addStretch(1)
        row.addLayout(col, 1)
        if self.url:
            ext = QLabel()
            ext.setPixmap(line_icon("link", TEXT_3, 14).pixmap(14, 14))
            row.addWidget(ext, 0, Qt.AlignmentFlag.AlignVCenter)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - a plain left click, released on the card
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.activate()
        super().mouseReleaseEvent(event)

    def activate(self) -> None:
        if not self.url:
            return
        try:
            open_url(self.url)
        except Exception as exc:  # noqa: BLE001 - no browser is not a reason to lose the chat
            print(f"[chat] não consegui abrir {self.url!r}: {exc}", flush=True)


# ---------------------------------------------------------------------- one turn
def _avatar(letter: str) -> QLabel:
    lab = QLabel(letter)
    lab.setFixedSize(24, 24)
    lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lab.setStyleSheet(f"background: {CONTROL}; color: {TEXT}; border-radius: 12px; font-size: 11px; font-weight: 600;")
    return lab


class ChatTurn(QWidget):
    """One question (a flat row) and Ombri's answer (a panel that is first the
    thinking row, then the streamed answer, then its cards)."""

    copy_done = Signal()
    confirm = Signal(bool)  # the typed note's "Sim, guardar" / "Não"

    def __init__(self, question: str, game: str | None, lang: str, typed: bool = False) -> None:
        super().__init__()
        self.lang = lang
        self.typed = typed
        self.question_text = question
        self.answer_text = ""
        self.game = game
        self.cards: list[dict] = []
        self.stage = ""          # one of STAGES while thinking, "" once the answer started
        self.finished = False
        self._piece_start = 0    # where the newest piece starts in answer_text (for its fade-in)
        self._piece_alpha = 1.0
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        # The question row: avatar, "Você", time; the text under it.
        self.q_row = QWidget()
        q = QVBoxLayout(self.q_row)
        q.setContentsMargins(SHADOW_PAD + 4, 4, SHADOW_PAD + 4, 2)
        q.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(_avatar(ui(lang, "chat_you")[:1]))
        head.addWidget(_label(ui(lang, "chat_you"), size=13, weight=600))
        self.q_time = _label(time.strftime("%H:%M"), size=12, color=TEXT_3)
        head.addWidget(self.q_time)
        head.addStretch(1)
        q.addLayout(head)
        self.question = QLabel()
        self.question.setWordWrap(True)
        self.question.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.question.setContentsMargins(32, 0, 0, 0)
        self.question.setStyleSheet(f"font-size: 14px; color: {TEXT};")
        q.addWidget(self.question)
        col.addWidget(self.q_row)
        self.set_question(question)

        # Ombri's panel.
        self.answer_card = Panel()
        a = QVBoxLayout(self.answer_card)
        a.setContentsMargins(SHADOW_PAD + 12, SHADOW_PAD + 8, SHADOW_PAD + 12, SHADOW_PAD + 10)
        a.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.mini = MiniOmbri(30)
        head.addWidget(self.mini)
        head.addWidget(_label(ui(lang, "chat_ombri"), size=13, weight=600))
        self.stage_label = ShimmerLabel()
        head.addWidget(self.stage_label)
        head.addStretch(1)
        self.dots = StepDots()
        head.addWidget(self.dots)
        self.copy_btn = IconButton("copy", ui(lang, "chat_copy"))
        self.copy_btn.clicked.connect(self._copy)
        self.copy_btn.hide()
        head.addWidget(self.copy_btn)
        a.addLayout(head)
        self.answer = QLabel()
        self.answer.setWordWrap(True)
        self.answer.setTextFormat(Qt.TextFormat.RichText)
        self.answer.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.answer.setContentsMargins(34, 0, 0, 0)
        self.answer.setStyleSheet(f"font-size: 14px; color: {TEXT};")
        self.answer.hide()
        a.addWidget(self.answer)
        # the brewing strip (client/gui/recipe_strip.py): part of the answer, it stays here
        self.recipe_host = QWidget()
        self.recipe_row = QHBoxLayout(self.recipe_host)
        self.recipe_row.setContentsMargins(30, 2, 0, 2)
        self.recipe_row.setSpacing(0)
        self.recipe_host.hide()
        self.recipe: dict | None = None
        a.addWidget(self.recipe_host)
        self.cards_host = QWidget()
        self.cards_grid = QGridLayout(self.cards_host)
        self.cards_grid.setContentsMargins(28, 2, 0, 0)
        self.cards_grid.setSpacing(0)
        self.cards_host.hide()
        a.addWidget(self.cards_host)
        self.note_row = QWidget()  # the "Guardo?" buttons of a typed note (set_confirm)
        self.note_row.hide()
        a.addWidget(self.note_row)
        col.addWidget(self.answer_card)
        self.set_stage("pensando" if question else "listening")

    # ---------------------------------------------------------------- the question
    def set_question(self, text: str) -> None:
        self.question_text = text
        if text:
            self.question.setText(html.escape(text))
            self.question.setStyleSheet(f"font-size: 14px; color: {TEXT};")
        else:  # F8 pressed, the words are still on their way
            self.question.setText(ui(self.lang, "chat_stage_listening"))
            self.question.setStyleSheet(f"font-size: 14px; color: {TEXT_3};")

    # ---------------------------------------------------------------- thinking
    def set_stage(self, stage: str) -> None:
        if self.answer_text or self.finished or stage not in STAGES:
            return
        self.stage = stage
        # While the player is still talking only their row shows ("Ouvindo…");
        # Ombri's panel fades in once there is something to think about.
        if stage == "listening":
            self.answer_card.hide()
        elif self.answer_card.isHidden():
            self.answer_card.show()
            motion.fade_in(self.answer_card)
        steps = {"listening": 0, "screen": 0, "pensando": 1, "pesquisando": 2}
        self.dots.set_step(steps[stage])
        self.stage_label.set_text(ui(self.lang, f"chat_stage_{stage}"), shimmer=True)
        self.mini.set_thinking(True)

    def _settle(self) -> None:
        self.stage = ""
        self.mini.set_thinking(False)
        self.dots.set_step(3, done=True)
        self.dots.hide()
        self.stage_label.set_text(time.strftime("%H:%M"), shimmer=False)
        self.copy_btn.setVisible(bool(self.answer_text))

    @property
    def thinking(self) -> bool:
        return bool(self.stage)

    # ---------------------------------------------------------------- the answer
    def append_answer(self, piece: str) -> None:
        first = not self.answer_text
        self._piece_start = len(self.answer_text)
        self.answer_text += piece
        if first:
            self.answer.show()
            self._settle()
        self._piece_alpha = 0.0
        self._render_answer()
        motion.tween(self, 0.0, 1.0, motion.SMALL, self._fade_piece)

    def _fade_piece(self, v: float) -> None:
        self._piece_alpha = v
        self._render_answer()

    def _render_answer(self) -> None:
        old = html.escape(self.answer_text[:self._piece_start])
        new = html.escape(self.answer_text[self._piece_start:])
        if self._piece_alpha >= 0.999 or not new:
            self.answer.setText(old + new)
            return
        c = QColor(TEXT)
        self.answer.setText(f"{old}<span style='color: rgba({c.red()},{c.green()},{c.blue()},"
                            f"{self._piece_alpha:.2f})'>{new}</span>")

    def finish(self, outcome: str = "") -> None:
        """The turn is over. With no answer at all, the panel eases into a
        short grey line (cancelled / no connection)."""
        if self.finished:
            return
        self.finished = True
        if not self.answer_text and outcome == "note":
            # a typed note: its result line (show_note_result) is the whole answer
            self.mini.set_thinking(False)
            self.answer_card.hide()
        elif not self.answer_text:
            key = {"failed": "chat_failed", "no_game": "chat_no_game",
                   "cap_reached": "chat_cap"}.get(outcome, "chat_cancelled")
            self.mini.set_thinking(False)
            self.dots.hide()
            self.stage = ""
            self.stage_label.set_text(ui(self.lang, key), shimmer=False)
        else:
            self._settle()

    # ---------------------------------------------------------------- a typed note's confirmation
    def set_confirm(self, note: str) -> None:
        """"Anotei que …. Guardo?" with two buttons. Nothing is saved without
        the "Sim" (principle 3)."""
        if self.note_row.layout() is None:
            row = QHBoxLayout(self.note_row)
            row.setContentsMargins(34, 4, 0, 0)
            row.setSpacing(8)
        row = self.note_row.layout()
        while row.count():
            w = row.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        yes = QPushButton(ui(self.lang, "chat_confirm_yes"))
        yes.setObjectName("primary")
        no = QPushButton(ui(self.lang, "chat_confirm_no"))
        no.setObjectName("small")
        for b in (yes, no):
            b.setFixedHeight(30)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(b)
        row.addStretch(1)
        yes.clicked.connect(lambda: self._answer_confirm(True))
        no.clicked.connect(lambda: self._answer_confirm(False))
        if not self.answer_text:
            self.append_answer(note)
        self.note_row.show()
        motion.fade_in(self.note_row)

    def _answer_confirm(self, yes: bool) -> None:
        for b in self.note_row.findChildren(QPushButton):
            b.setEnabled(False)
        self.confirm.emit(yes)

    def show_note_result(self, saved: bool) -> None:
        """The buttons fold into one line with a tick ("Guardado") or a plain "Não guardei"."""
        row = self.note_row.layout()
        if row is None:
            return
        while row.count():
            w = row.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        ic = QLabel()
        ic.setPixmap(line_icon("check" if saved else "close", OK_GREEN if saved else TEXT_3, 14).pixmap(14, 14))
        row.addWidget(ic)
        row.addWidget(_label(ui(self.lang, "chat_saved" if saved else "chat_not_saved"), size=13,
                             color=TEXT if saved else TEXT_3))
        row.addStretch(1)
        motion.fade_in(self.note_row, rise=0)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.answer_text)
        self.copy_btn.flash_check()
        self.copy_done.emit()

    # ---------------------------------------------------------------- cards
    def set_cards(self, cards: list[dict]) -> None:
        while self.cards_grid.count():
            widget = self.cards_grid.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.cards = cards[:MAX_CARDS_PER_TURN]
        for i, card in enumerate(self.cards):
            w = ChatCard(card, self.lang)
            self.cards_grid.addWidget(w, i // CARD_COLUMNS, i % CARD_COLUMNS)
            motion.fade_in(w, delay=i * motion.STAGGER)
        self.cards_host.setVisible(bool(self.cards))

    def add_card(self, card: dict, thumb: QImage | None = None) -> None:
        """One more card under this answer (the in-game pane's mirror of a
        picture it showed), after the ones the server sent."""
        i = len(self.cards)
        self.cards.append(card)
        w = ChatCard(card, self.lang, thumb)
        self.cards_grid.addWidget(w, i // CARD_COLUMNS, i % CARD_COLUMNS)
        motion.fade_in(w)
        self.cards_host.show()
        if self.answer_card.isHidden():
            self.answer_card.show()

    def card_widgets(self) -> list[ChatCard]:
        return [w for w in self.cards_host.findChildren(ChatCard)]

    def set_recipe(self, card: dict) -> None:
        """The brewing strip under the answer (one per turn; a second card
        replaces the first). The panel shows at once, before the answer's
        first word, as the strip arrives right after the question."""
        from client.gui.recipe_strip import RecipeStrip

        while self.recipe_row.count():
            widget = self.recipe_row.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.recipe = card
        strip = RecipeStrip(card, glass=False, max_w=CHAT_W - 2 * SHADOW_PAD - 60)
        self.recipe_row.addWidget(strip, 0, Qt.AlignmentFlag.AlignLeft)
        self.recipe_row.addStretch(1)
        self.recipe_host.show()
        if self.answer_card.isHidden():
            self.answer_card.show()
        motion.fade_in(strip)

    def recipe_widget(self):
        from client.gui.recipe_strip import RecipeStrip

        found = self.recipe_host.findChildren(RecipeStrip)
        return found[0] if found else None


# ---------------------------------------------------------------------- the list host (paints the glow)
class _ListHost(QWidget):
    """The scrolling column. It paints the newest Ombri panel's glow behind it
    (a child can't paint outside itself), breathing slowly and quietly."""

    def __init__(self) -> None:
        super().__init__()
        self.target: Panel | None = None
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def _tick(self) -> None:
        if self.target is not None and not motion.quiet(self):
            r = self._glow_rect()
            if r is not None:
                pad = motion.GLOW_SPREAD + 2
                self.update(r.adjusted(-pad, -pad, pad, pad).toAlignedRect())

    def _glow_rect(self) -> QRectF | None:
        t = self.target
        if t is None or not t.isVisible():
            return None
        top_left = t.mapTo(self, t.panel_rect().topLeft().toPoint())
        return QRectF(top_left.x(), top_left.y(), t.panel_rect().width(), t.panel_rect().height())

    def level(self) -> float:
        if not motion.enabled():
            return motion.GLOW_MIN_ALPHA
        import math
        phase = ((time.monotonic() - self._t0) % motion.LOOP_GLOW) / motion.LOOP_GLOW
        k = 0.5 - 0.5 * math.cos(phase * 2 * math.pi)
        return motion.GLOW_MIN_ALPHA + (motion.GLOW_MAX_ALPHA - motion.GLOW_MIN_ALPHA) * k

    def paintEvent(self, event) -> None:  # noqa: N802
        r = self._glow_rect()
        if r is not None:
            p = QPainter(self)
            paint_glow(p, r, self.level())
            p.end()


# ---------------------------------------------------------------------- tabs
class TabBar(QWidget):
    """Conversa / Links with a pill that slides to the active tab, and the
    "‹" that closes the chat."""

    changed = Signal(int)
    close_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(44)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 10, 4)
        row.setSpacing(4)
        self.buttons: list[QPushButton] = []
        for i in range(2):
            b = QPushButton()
            b.setObjectName("tabbtn")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(30)
            b.clicked.connect(lambda _=False, i=i: self.select(i))
            row.addWidget(b)
            self.buttons.append(b)
        row.addStretch(1)
        self.close_btn = IconButton("chevron_left", "", 30, 16)
        self.close_btn.clicked.connect(self.close_requested.emit)
        row.addWidget(self.close_btn)
        self.index = 0
        self._pill = QRectF()
        self._bump = 0.0

    def set_texts(self, talk: str, links: str, close_tip: str) -> None:
        self.buttons[0].setText(talk)
        self.buttons[1].setText(links)
        self.close_btn.setToolTip(close_tip)
        f = QFont(BODY_FAMILY)
        f.setPixelSize(13)
        f.setWeight(QFont.Weight.DemiBold)
        for b in self.buttons:
            b.setFixedWidth(QFontMetrics(f).horizontalAdvance(b.text()) + 32)
        QTimer.singleShot(0, self, lambda: self._move_pill(animate=False))

    def select(self, index: int, emit: bool = True) -> None:
        self.index = index
        for i, b in enumerate(self.buttons):
            b.setProperty("on", i == index)
            b.style().unpolish(b)
            b.style().polish(b)
        self._move_pill(animate=True)
        if emit:
            self.changed.emit(index)

    def _move_pill(self, animate: bool) -> None:
        """The pill moves like a drop (2026-11-23): its centre and width ride springs,
        it stretches along the way it travels and thins a little, and a smaller tail
        follows behind joined to it, so it pulls apart and merges back like water."""
        b = self.buttons[self.index]
        target = QRectF(b.geometry())
        if not hasattr(self, "_px"):
            self._px = motion.Spring(self, target.center().x(), lambda _x: self.update(), *motion.SPRING_DROP)
            self._pw = motion.Spring(self, target.width(), lambda _x: self.update(), *motion.SPRING_DROP)
            self._tail = motion.Spring(self, target.center().x(), lambda _x: self.update(), 2.2, 0.9)
        self._pill_y, self._pill_h = target.y(), target.height()
        if not animate or self._pill.isNull():
            self._px.jump(target.center().x())
            self._pw.jump(target.width())
            self._tail.jump(target.center().x())
        else:
            self._px.to(target.center().x())
            self._pw.to(target.width())
            self._tail.to(target.center().x())
        self._pill = target
        self.update()

    def _pill_paint(self, p) -> None:
        if hasattr(self, "_px"):
            motion.paint_drop(p, self._px, self._pw, self._tail, self._pill_y, self._pill_h)
        else:
            p.drawRoundedRect(self._pill.adjusted(0.5, 0.5, -0.5, -0.5), 15, 15)

    def bump(self) -> None:
        """The Links count just went up: a short coral flash on its tab."""
        def step(v: float) -> None:
            self._bump = v
            self.update()
        motion.tween(self, 1.0, 0.0, motion.LAYOUT * 2, step)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._move_pill(animate=False)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._pill.isNull():
            p.setPen(QPen(HAIR, 1))
            p.setBrush(QColor(CONTROL))
            self._pill_paint(p)
        if self._bump > 0.01:
            b = QRectF(self.buttons[1].geometry())
            c = QColor(ACCENT)
            c.setAlphaF(0.35 * self._bump)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(b, 15, 15)
        p.end()


# ---------------------------------------------------------------------- the input box
class _Editor(QPlainTextEdit):
    send = Signal()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Enter sends, Shift+Enter is a new line
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.send.emit()
            return
        super().keyPressEvent(event)


class Chip(QPushButton):
    """A round chip (the game, "Lembrar"). `checkable` chips fill blue when on."""

    def __init__(self, checkable: bool = False) -> None:
        super().__init__()
        self.setObjectName("chatchip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(28)
        self.setCheckable(checkable)


class ChatInput(Panel):
    """The box: the text, the game chip, "Lembrar", the mic and send. Focus
    brightens the hairline toward coral; the send button turns coral as soon
    as there's text and dims back when empty."""

    send_requested = Signal(str, bool)     # text, remember
    mic_requested = Signal()
    game_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.lang = "pt"
        self.keys = {"ask": "F8"}
        box = QVBoxLayout(self)
        box.setContentsMargins(SHADOW_PAD + 12, SHADOW_PAD + 8, SHADOW_PAD + 10, SHADOW_PAD + 10)
        box.setSpacing(8)
        self.edit = _Editor()
        self.edit.setObjectName("chatedit")
        self.edit.setTabChangesFocus(True)
        self.edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.edit.setFixedHeight(44)
        self.edit.textChanged.connect(self._changed)
        self.edit.send.connect(self.submit)
        self.edit.installEventFilter(self)
        box.addWidget(self.edit)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.game_chip = Chip()
        self.game_chip.clicked.connect(self.game_requested.emit)
        row.addWidget(self.game_chip)
        self.remember_chip = Chip(checkable=True)
        self.remember_chip.toggled.connect(self._remember_toggled)
        row.addWidget(self.remember_chip)
        self.hint = _label("", size=11, color=TEXT_3)
        row.addWidget(self.hint)
        row.addStretch(1)
        self.mic = MicButton()
        self.mic.clicked.connect(self.mic_requested.emit)
        row.addWidget(self.mic)
        self.send_btn = SendButton()
        self.send_btn.clicked.connect(self.submit)
        row.addWidget(self.send_btn)
        box.addLayout(row)
        self._focus = 0.0

    def set_language(self, lang: str, keys: dict) -> None:
        self.lang = lang
        self.keys = dict(keys)
        self.remember_chip.setText(ui(lang, "chat_remember"))
        self._remember_toggled(self.remember_chip.isChecked())
        self.mic.setToolTip(ui(lang, "chat_mic", ask=keys.get("ask", "F8")))
        self.send_btn.setToolTip(ui(lang, "chat_send"))

    def set_game(self, game: str | None) -> None:
        self.game_chip.setText(f"{game}  ▾" if game else "—  ▾")

    def _remember_toggled(self, on: bool) -> None:
        self.edit.setPlaceholderText(ui(self.lang, "chat_remember_placeholder" if on else "chat_placeholder"))
        self.remember_chip.setIcon(line_icon("check" if on else "note", "#14161C" if on else TEXT_2, 13))

    def _changed(self) -> None:
        text = self.edit.toPlainText()
        if len(text) > MESSAGE_MAX:
            cursor = self.edit.textCursor()
            self.edit.blockSignals(True)
            self.edit.setPlainText(text[:MESSAGE_MAX])
            self.edit.blockSignals(False)
            cursor.setPosition(MESSAGE_MAX)
            self.edit.setTextCursor(cursor)
            text = text[:MESSAGE_MAX]
        self.hint.setText(ui(self.lang, "chat_too_long", n=MESSAGE_MAX) if len(text) >= MESSAGE_MAX - 20 else "")
        self.send_btn.set_ready(bool(text.strip()))
        # Grow with the text, 1 to 5 lines.
        doc_h = int(self.edit.document().size().height() * QFontMetrics(self.edit.font()).lineSpacing()) + 14
        self.edit.setFixedHeight(max(44, min(120, doc_h)))

    def submit(self) -> None:
        text = " ".join(self.edit.toPlainText().split())
        if not text:
            return
        remember = self.remember_chip.isChecked()
        self.send_btn.press()
        self.edit.clear()
        self.remember_chip.setChecked(False)
        self.send_requested.emit(text, remember)

    def fill(self, text: str) -> None:
        """A suggestion clicked: its words typed into the box, fast."""
        self.edit.clear()
        self.edit.setFocus()
        if not motion.enabled():
            self.edit.setPlainText(text)
            self.edit.moveCursor(self.edit.textCursor().MoveOperation.End)
            return

        def step(v: float) -> None:
            self.edit.setPlainText(text[:int(round(v))])
            self.edit.moveCursor(self.edit.textCursor().MoveOperation.End)
        motion.tween(self, 0.0, float(len(text)), min(400, 18 * len(text)), step,
                     curve=motion.QEasingCurve.Type.Linear)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - focus: the hairline warms toward coral
        if obj is self.edit and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            to = 1.0 if event.type() == QEvent.Type.FocusIn else 0.0
            motion.tween(self, self._focus, to, motion.FAST, self._set_focus)
        return super().eventFilter(obj, event)

    def _set_focus(self, v: float) -> None:
        self._focus = v
        c = QColor(ACCENT)
        base = QColor(HAIR)
        mix = QColor(int(base.red() + (c.red() - base.red()) * v), int(base.green() + (c.green() - base.green()) * v),
                     int(base.blue() + (c.blue() - base.blue()) * v), int(base.alpha() + (80 - base.alpha()) * v))
        self.set_hair(mix)


class SendButton(QPushButton):
    """Grey when the box is empty, coral when there's something to send (the
    colour eases between the two); a short press on send."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._k = 0.0
        self._press = 0.0
        self.setStyleSheet("background: transparent; border: none;")

    def set_ready(self, on: bool) -> None:
        target = 1.0 if on else 0.0
        if abs(target - self._k) < 0.01:
            return
        motion.tween(self, self._k, target, motion.SMALL, self._set_k)

    def _set_k(self, v: float) -> None:
        self._k = v
        self.update()

    def press(self) -> None:
        def step(v: float) -> None:
            self._press = v
            self.update()
        motion.tween(self, 1.0, 0.0, motion.SMALL, step)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a, b = QColor(CONTROL), QColor(ACCENT)
        k = self._k
        fill = QColor(int(a.red() + (b.red() - a.red()) * k), int(a.green() + (b.green() - a.green()) * k),
                      int(a.blue() + (b.blue() - a.blue()) * k))
        s = 1.0 - 0.06 * self._press
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        r = QRectF(r.center().x() - r.width() * s / 2, r.center().y() - r.height() * s / 2, r.width() * s,
                   r.height() * s)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(r, 8, 8)
        ink = QColor("#14161C") if k > 0.5 else QColor(TEXT_3)
        pen = QPen(ink, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        c = r.center()
        p.drawLine(QPointF(c.x(), c.y() + 6), QPointF(c.x(), c.y() - 6))
        p.drawLine(QPointF(c.x() - 5, c.y() - 1), QPointF(c.x(), c.y() - 6))
        p.drawLine(QPointF(c.x() + 5, c.y() - 1), QPointF(c.x(), c.y() - 6))
        p.end()


class MicButton(QPushButton):
    """The mic (= F8). While Ombri listens: a blue ring pulses and five bars
    follow the real mic level (`set_level`)."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("background: transparent; border: none;")
        self.listening = False
        self._level = 0.0
        self._hover = 0.0
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def set_listening(self, on: bool) -> None:
        self.listening = on
        if on:
            self._timer.start(33)
        else:
            self._timer.stop()
            self._level = 0.0
        self.update()

    def set_level(self, level: float) -> None:
        self._level = max(self._level, max(0.0, min(1.0, level)))

    def _tick(self) -> None:
        self._level *= 0.85
        if not motion.quiet(self):
            self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        motion.tween(self, self._hover, 1.0, motion.FAST, self._set_hover)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        motion.tween(self, self._hover, 0.0, motion.FAST, self._set_hover)
        super().leaveEvent(event)

    def _set_hover(self, v: float) -> None:
        self._hover = v
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        if self.listening:
            t = time.monotonic() - self._t0
            k = 0.5 - 0.5 * math.cos(t * 2 * math.pi / 1.4)
            ring = QColor(OK_GREEN)
            ring.setAlphaF(0.25 + 0.35 * k)
            p.setPen(QPen(ring, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, 13 + 2 * k, 13 + 2 * k)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(OK_GREEN))
            for i in range(5):
                wob = 0.35 + 0.65 * abs(math.sin(t * 7 + i * 1.3))
                h = 3 + 12 * self._level * wob
                p.drawRoundedRect(QRectF(c.x() - 9 + i * 4, c.y() - h / 2, 2.4, h), 1.2, 1.2)
        else:
            if self._hover:
                bg = QColor(CONTROL)
                bg.setAlphaF(self._hover)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(bg)
                p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 8, 8)
            p.drawPixmap(8, 8, line_icon("mic", TEXT if self._hover > 0.5 else TEXT_2, 16).pixmap(16, 16))
        p.end()


# ---------------------------------------------------------------------- empty state
class Suggestion(Panel):
    clicked = Signal(str)

    def __init__(self, icon: str) -> None:
        super().__init__(hover=True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        col = QVBoxLayout(self)
        col.setContentsMargins(SHADOW_PAD + 12, SHADOW_PAD + 8, SHADOW_PAD + 10, SHADOW_PAD + 10)
        col.setSpacing(3)
        ic = QLabel()
        ic.setPixmap(line_icon(icon, TEXT_3, 16).pixmap(16, 16))
        col.addWidget(ic)
        self.title = _label("", size=13, weight=600)
        self.title.setWordWrap(True)
        col.addWidget(self.title)
        self.sub = _label("", size=12, color=TEXT_3)
        self.sub.setWordWrap(True)
        col.addWidget(self.sub)
        col.addStretch(1)
        self.fill_text = ""

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.fill_text)
        super().mouseReleaseEvent(event)


class EmptyState(QWidget):
    fill_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        col = QVBoxLayout(self)
        col.setContentsMargins(18, 0, 18, 0)
        col.setSpacing(8)
        col.addStretch(1)
        self.title = _label("", size=19, weight=600, heading=True)
        self.title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.title.setWordWrap(True)
        col.addWidget(self.title)
        self.sub = _label("", size=13, color=TEXT_2)
        self.sub.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.sub.setWordWrap(True)
        col.addWidget(self.sub)
        col.addSpacing(10)
        grid = QHBoxLayout()
        grid.setSpacing(0)
        self.suggestions = [Suggestion("chat"), Suggestion("cube"), Suggestion("note")]
        for s in self.suggestions:
            s.clicked.connect(self.fill_requested.emit)
            grid.addWidget(s, 1)
        col.addLayout(grid)
        col.addStretch(2)

    def set_texts(self, lang: str, game: str | None) -> None:
        self.title.setText(ui(lang, "chat_empty_title", game=game) if game else ui(lang, "chat_empty_title_nogame"))
        self.sub.setText(ui(lang, "chat_empty_sub"))
        for i, s in enumerate(self.suggestions, 1):
            s.title.setText(ui(lang, f"chat_sug_{i}"))
            s.sub.setText(ui(lang, f"chat_sug_{i}_sub"))
            s.fill_text = ui(lang, f"chat_sug_{i}") + ("" if i == 1 else " ")


# ---------------------------------------------------------------------- links
def _day_label(lang: str, ts: float) -> str:
    d = datetime.fromtimestamp(ts).date()
    today = date.today()
    if d == today:
        return ui(lang, "chat_today")
    if (today - d).days == 1:
        return ui(lang, "chat_yesterday")
    return d.strftime("%m/%d/%Y") if lang == "en" else d.strftime("%d/%m/%Y")


class LinkRow(ChatCard):
    """A saved link: the card look, the game after the site."""

    def __init__(self, link: dict, lang: str) -> None:
        super().__init__(link, lang)
        meta = link.get("site") or ""
        if link.get("game"):
            meta = f"{meta} · {link['game']}" if meta else str(link["game"])
        self.site.setText(meta)
        self.link = link


class LinksView(QWidget):
    """Every link Ombri sent, newest first, grouped by day; a search over the
    title and site, and a game filter. Rows fade in one after another."""

    def __init__(self) -> None:
        super().__init__()
        self.lang = "pt"
        self.links: list[dict] = []
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 6, 14, 0)
        col.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("chatsearch")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.render())
        top.addWidget(self.search, 1)
        self.game = QComboBox()
        self.game.setObjectName("chatcombo")
        self.game.currentIndexChanged.connect(lambda _i: self.render())
        top.addWidget(self.game)
        col.addLayout(top)
        self.status = _label("", size=13, color=TEXT_2)
        self.status.setWordWrap(True)
        col.addWidget(self.status)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.host = QWidget()
        self.list = QVBoxLayout(self.host)
        self.list.setContentsMargins(0, 0, 4, 12)
        self.list.setSpacing(0)
        self.list.addStretch(1)
        self.scroll.setWidget(self.host)
        col.addWidget(self.scroll, 1)

    def set_language(self, lang: str) -> None:
        self.lang = lang
        self.search.setPlaceholderText(ui(lang, "links_search"))
        self._fill_games()
        self.render()

    def set_links(self, links: list[dict]) -> None:
        self.links = list(links)
        self._fill_games()
        self.render()

    def _fill_games(self) -> None:
        current = self.game.currentData()
        games = sorted({str(l.get("game")) for l in self.links if l.get("game")})
        self.game.blockSignals(True)
        self.game.clear()
        self.game.addItem(ui(self.lang, "links_all_games"), None)
        for g in games:
            self.game.addItem(g, g)
        i = self.game.findData(current)
        self.game.setCurrentIndex(max(0, i))
        self.game.blockSignals(False)

    def shown(self) -> list[dict]:
        q = self.search.text().strip().lower()
        g = self.game.currentData()
        out = []
        for link in self.links:
            if g and link.get("game") != g:
                continue
            if q and q not in f"{link.get('title', '')} {link.get('site', '')}".lower():
                continue
            out.append(link)
        return out

    def render(self) -> None:
        while self.list.count() > 1:
            w = self.list.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        rows = self.shown()
        if not self.links:
            self.status.setText(ui(self.lang, "links_empty"))
        elif not rows:
            self.status.setText(ui(self.lang, "links_none_found"))
        else:
            self.status.setText("")
        self.status.setVisible(bool(self.status.text()))
        last_day = None
        n = 0
        for link in rows:
            clean = clean_card(link)
            if clean is None:
                continue
            clean["game"] = link.get("game") or ""
            day = _day_label(self.lang, float(link.get("ts") or 0))
            if day != last_day:
                caps = _label(day.upper(), name="caps")
                caps.setContentsMargins(SHADOW_PAD + 2, 10 if last_day else 2, 0, 2)
                self.list.insertWidget(self.list.count() - 1, caps)
                last_day = day
            row = LinkRow(clean, self.lang)
            self.list.insertWidget(self.list.count() - 1, row)
            if n < 12:  # the first screenful staggers in; the rest just appear
                motion.fade_in(row, delay=n * motion.STAGGER // 2)
            n += 1

    def rows(self) -> list[LinkRow]:
        return self.host.findChildren(LinkRow)


# ---------------------------------------------------------------------- the chat
class _RoomForStrip(QObject):
    """Gives the conversation its bottom margin back when the hint strip hides
    (timed out, ticked off, replaced). Owned by the strip, dies with it."""

    def __init__(self, strip, on_close) -> None:
        super().__init__(strip)
        self.on_close = on_close

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Hide and obj.isHidden():
            self.on_close()
        return False


class ChatView(QWidget):
    """The whole chat: tab bar, the conversation (or its empty state) with the
    input box, and the Links tab. ui_process / main_window feed it."""

    send_requested = Signal(str, bool)     # text, remember -> main.py "chat_send"
    mic_requested = Signal()               # the mic button -> main.py "chat_mic" (F8)
    game_requested = Signal()              # the game chip -> the game picker
    close_requested = Signal()
    confirm_answered = Signal(bool)        # a typed note's "Sim, guardar" / "Não"

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("chatroot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.lang = "pt"
        self.keys = {"ask": "F8", "note": "F6"}
        self.game: str | None = None
        self.turns: list[ChatTurn] = []
        self._follow = True
        self._links_count = 0
        self.coach = hints_mod.Hints()  # the window swaps in its shared one
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.tabs = TabBar()
        self.tabs.changed.connect(self._tab_changed)
        self.tabs.close_requested.connect(self.close_requested.emit)
        col.addWidget(self.tabs)
        self.banner = _label("", size=12, color="#14161C")
        self.banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner.setStyleSheet(f"background: {ACCENT}; color: #14161C; font-size: 12px; font-weight: 600;")
        self.banner.setMaximumHeight(0)
        col.addWidget(self.banner)
        self.pages = QStackedWidget()
        col.addWidget(self.pages, 1)

        # Conversa
        talk = QWidget()
        t = QVBoxLayout(talk)
        t.setContentsMargins(8, 0, 8, 8)
        t.setSpacing(4)
        self.body = QStackedWidget()
        self.empty = EmptyState()
        self.body.addWidget(self.empty)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.host = _ListHost()
        self.list = QVBoxLayout(self.host)
        self._list_margins = (4, 8, 6, 12)
        self.list.setContentsMargins(*self._list_margins)
        self.list.setSpacing(8)
        self.list.addStretch(1)
        self.scroll.setWidget(self.host)
        bar = self.scroll.verticalScrollBar()
        bar.rangeChanged.connect(self._range_changed)
        bar.valueChanged.connect(self._value_changed)
        self.body.addWidget(self.scroll)
        t.addWidget(self.body, 1)
        self.new_pill = QPushButton(self.scroll)
        self.new_pill.setObjectName("newpill")
        self.new_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_pill.clicked.connect(self._jump_to_new)
        self.new_pill.hide()
        self.input = ChatInput()
        self.input.send_requested.connect(self._send)
        self.input.mic_requested.connect(self.mic_requested.emit)
        self.input.game_requested.connect(self.game_requested.emit)
        self.empty.fill_requested.connect(self.input.fill)
        t.addWidget(self.input)
        self.pages.addWidget(talk)

        # Links
        self.links = LinksView()
        self.pages.addWidget(self.links)
        self.set_language("pt", self.keys)

    # ---------------------------------------------------------------- texts
    def set_language(self, lang: str, keys: dict) -> None:
        self.lang = lang
        self.keys = dict(keys)
        self.input.set_language(lang, self.keys)
        self.links.set_language(lang)
        self.empty.set_texts(lang, self.game)
        self.new_pill.setText(f"{ui(lang, 'chat_new_answer')}  ↓")
        self.new_pill.adjustSize()
        self._tab_texts()

    def _tab_texts(self) -> None:
        links = ui(self.lang, "chat_tab_links")
        if self._links_count:
            links = f"{links}  {self._links_count}"
        self.tabs.set_texts(ui(self.lang, "chat_tab_talk"), links, ui(self.lang, "chat_close"))

    def set_game(self, game: str | None) -> None:
        self.game = game
        self.input.set_game(game)
        self.empty.set_texts(self.lang, game)

    def set_connection(self, conn: str) -> None:
        """Offline: a slim coral banner slides down under the tabs; back online, it slides away."""
        show = conn == "offline"
        self.banner.setText(ui(self.lang, "chat_offline"))
        target = 24 if show else 0
        start = self.banner.maximumHeight()
        if start != target:
            motion.tween(self, float(start), float(target), motion.SMALL,
                         lambda v: self.banner.setMaximumHeight(int(round(v))))

    # ---------------------------------------------------------------- tabs
    def _tab_changed(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        motion.fade_in(self.pages.currentWidget(), ms=motion.SMALL, rise=0)

    def open_tab(self, index: int) -> None:
        self.tabs.select(index, emit=False)
        self.pages.setCurrentIndex(index)

    # ---------------------------------------------------------------- links
    def set_links(self, links: list[dict]) -> None:
        grew = len(links) > self._links_count and self._links_count > 0
        self._links_count = len(links)
        self.links.set_links(links)
        self._tab_texts()
        if grew:
            self.tabs.bump()

    # ---------------------------------------------------------------- turns
    def _show_list(self) -> None:
        if self.body.currentWidget() is not self.scroll:
            self.body.setCurrentWidget(self.scroll)
            motion.fade_in(self.scroll, rise=0)

    def _add_turn(self, question: str, game: str | None, typed: bool) -> ChatTurn:
        turn = ChatTurn(question, game, self.lang, typed=typed)
        turn.confirm.connect(self.confirm_answered.emit)
        self.turns.append(turn)
        self.list.addWidget(turn)
        while len(self.turns) > MAX_TURNS:
            old = self.turns.pop(0)
            self.list.removeWidget(old)
            old.setParent(None)
            old.deleteLater()
        self._show_list()
        motion.fade_in(turn)
        self.host.target = turn.answer_card
        self._follow = True
        QTimer.singleShot(0, self._scroll_to_bottom)
        return turn

    def _pending(self) -> ChatTurn | None:
        """The newest turn if it's still waiting for its question (F8) or
        for the server's echo of a typed one."""
        if self.turns and not self.turns[-1].finished and not self.turns[-1].answer_text:
            return self.turns[-1]
        return None

    def _send(self, text: str, remember: bool) -> None:
        turn = self._add_turn(text, self.game, typed=True)
        turn.set_stage("pensando")
        self.send_requested.emit(text, remember)
        if not remember:  # a typed question goes without the screen (the key and the mic send it); a note needs none
            self._teach("typed_no_screen")

    def add_question(self, text: str, game: str | None = None) -> ChatTurn | None:
        """The turn's question from the server (turn_meta). It fills a pending
        row (F8 in progress, or the typed echo) instead of adding a second one."""
        text = " ".join(str(text or "").split())
        if not text:
            return None
        pending = self._pending()
        if pending is not None and (not pending.question_text or pending.question_text == text):
            pending.set_question(text)
            pending.set_stage("pensando")
            return pending
        turn = self._add_turn(text, game or None, typed=False)
        return turn

    def set_state(self, state: str) -> None:
        """The client's own state (the orb's): a press of F8 opens a pending
        row ("Ouvindo…"); pensando / pesquisando move the thinking row on;
        back to idle closes a turn that got nothing."""
        pending = self._pending()
        if state == "ouvindo":
            if pending is None and not self._mid_turn():
                self._add_turn("", self.game, typed=False)
            self.input.mic.set_listening(True)
            return
        self.input.mic.set_listening(False)
        if state in ("pensando", "pesquisando") and pending is not None:
            pending.set_stage(state)
        elif state in ("idle", "pausado") and self.turns:
            last = self.turns[-1]
            if not last.finished and not last.typed:  # a typed turn ends with turn_done (chat_done)
                if not last.question_text and not last.answer_text:
                    self._drop(last)  # F8 with nothing said: the row goes away
                else:
                    last.finish("" if last.answer_text else "cancelled")

    def _mid_turn(self) -> bool:
        return bool(self.turns) and not self.turns[-1].finished and bool(self.turns[-1].answer_text)

    def set_stage(self, stage: str) -> None:
        """Extra stages the client knows (the screenshot was taken: "screen")."""
        pending = self._pending()
        if pending is not None:
            pending.set_stage(stage)

    def set_voice_level(self, level: float) -> None:
        if self.input.mic.listening:
            self.input.mic.set_level(level)

    def turn_failed(self) -> None:
        if self.turns and not self.turns[-1].finished:
            self.turns[-1].finish("failed")

    def turn_done(self, outcome: str) -> None:
        """main.py says the turn ended. A note's outcome folds its buttons."""
        if not self.turns:
            return
        last = self.turns[-1]
        note = outcome in ("note_saved", "note_discarded", "note_empty")
        if note and last.note_row.layout() is not None:
            last.show_note_result(outcome == "note_saved")
        if not last.finished:
            last.finish("note" if note and last.note_row.layout() is not None else outcome)
        if outcome == "note_saved":
            self._teach("note_recall")
        elif outcome == "answered" and last.answer_text:  # spoken (a typed turn ends answered_no_audio)
            self._teach("chat_repeat")

    def ask_confirm(self, note: str) -> None:
        turn = self._current()
        turn.set_confirm(" ".join(str(note or "").split()))
        self._after_growth()

    def _drop(self, turn: ChatTurn) -> None:
        if turn in self.turns:
            self.turns.remove(turn)
        self.list.removeWidget(turn)
        turn.setParent(None)
        turn.deleteLater()
        self.host.target = self.turns[-1].answer_card if self.turns else None
        if not self.turns:
            self.body.setCurrentWidget(self.empty)

    def _current(self) -> ChatTurn:
        if not self.turns:
            return self._add_turn("", None, typed=False)
        return self.turns[-1]

    def append_answer(self, piece: str) -> None:
        piece = str(piece or "")
        if not piece:
            return
        self._current().append_answer(piece)
        self._after_growth()

    def set_cards(self, cards: list) -> None:
        clean = [c for c in (clean_card(raw) for raw in (cards or [])) if c is not None]
        if not clean:
            return
        self._current().set_cards(clean)
        self._after_growth()
        self._teach("chat_links")

    def _teach(self, hint_id: str) -> bool:
        """A hint along the bottom of the conversation - only while the
        player can see it (the view shown, the Conversa tab in front). The
        last row is not covered: the list gets the strip's height at its
        bottom while the strip is up (2026-11-26, with `ask_again` over the
        "Ouvindo…" row), and follows to the bottom."""
        # closed, the chat is clipped to nothing beside the home column, not hidden: ask for the region
        if self.visibleRegion().isEmpty() or self.pages.currentIndex() != 0 or self.body.currentWidget() is not self.scroll:
            return False
        if not self.coach.show(hint_id, self.body, self.lang, ask=self.keys.get("ask", "")):
            return False
        strip = self.coach.strip(hint_id)
        if strip is not None:
            QTimer.singleShot(0, lambda: self._make_room(strip))  # after the strip's own first placement
        return True

    def _make_room(self, strip) -> None:
        try:
            if strip.isHidden() or strip.leaving:
                return
            left, top, right, bottom = self._list_margins
            self.list.setContentsMargins(left, top, right, bottom + strip.height() + hints_mod.MARGIN)
            strip.installEventFilter(_RoomForStrip(strip, self._room_back))
            self._after_growth()
        except RuntimeError:  # the strip already gone
            pass

    def _room_back(self) -> None:
        self.list.setContentsMargins(*self._list_margins)

    def add_visual(self, item: dict, image: QImage | None) -> dict:
        """A picture the in-game pane showed (client/gui/game_pane.py): a card
        with its thumbnail and source under the newest answer - the pane is a
        view of the conversation, never a second one. Returns the card."""
        card = visual_card(item)
        self._current().add_card(card, image)
        self._after_growth()
        return card

    def set_recipe(self, card: dict) -> None:
        """The brewing strip (a `recipe` card, already cleaned by the window)
        under the newest answer."""
        if not isinstance(card, dict) or not card.get("steps"):
            return
        self._current().set_recipe(card)
        self._after_growth()

    def refresh_recipe_icons(self) -> None:
        for turn in self.turns:
            strip = turn.recipe_widget() if turn.recipe is not None else None
            if strip is not None:
                strip.refresh_icons()

    def _after_growth(self) -> None:
        if self._follow:
            QTimer.singleShot(0, self._scroll_to_bottom)
        elif self.isVisible():
            self._show_pill()

    # ---------------------------------------------------------------- scrolling
    def _scroll_to_bottom(self, animate: bool = False) -> None:
        bar = self.scroll.verticalScrollBar()
        if animate and motion.enabled():
            motion.tween(self, float(bar.value()), float(bar.maximum()), motion.LAYOUT,
                         lambda v: bar.setValue(int(round(v))))
        else:
            bar.setValue(bar.maximum())

    def _range_changed(self, _lo: int, hi: int) -> None:
        if self._follow:
            self.scroll.verticalScrollBar().setValue(hi)

    def _value_changed(self, value: int) -> None:
        bar = self.scroll.verticalScrollBar()
        self._follow = value >= bar.maximum() - 4
        if self._follow and self.new_pill.isVisible():
            self.new_pill.hide()

    def _show_pill(self) -> None:
        if self.new_pill.isVisible():
            return
        self.new_pill.adjustSize()
        x = (self.scroll.width() - self.new_pill.width()) // 2
        end_y = self.scroll.height() - self.new_pill.height() - 12
        self.new_pill.move(x, end_y + 20)
        self.new_pill.show()
        self.new_pill.raise_()
        motion.tween(self, float(end_y + 20), float(end_y), motion.SMALL,
                     lambda v: self.new_pill.move(x, int(round(v))))

    def _jump_to_new(self) -> None:
        self.new_pill.hide()
        self._follow = True
        self._scroll_to_bottom(animate=True)
