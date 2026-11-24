"""client/gui/main_window.py — Ombri's desktop window (stage 1, 2026-09-29).

Built to the approved mockup (the "Janela principal" and "Sem jogo aberto"
screens): a dark, frameless, rounded window with its own title bar. Before a
game is detected it shows the "Abra um jogo" view; from the first detected
game on, the status view: the orb and its state, the connection, the game,
questions left today, the hotkeys, the voice. Minimize and close both hide
it to the tray - Ombri keeps running.

Pure view: it never talks to the voice loop itself. ui_process.py feeds it
(set_state, set_game, ...) and relays its buttons as commands.

Since 2026-11-23 (docs/chat_redesign.md) there is no top nav. The home column
(440 px, the mascot exactly where it always was) has a coral "Conversar com o
Ombri" button; it widens the same window to the right and the chat
(client/gui/chat_view.py) opens beside the home column, which never moves.
"Minhas notas" and "Configurações" open in the home column from their
buttons, each with a "Voltar". The stack's pages: 0 waiting, 1 status,
2 notes, 3 settings.

Since 2026-11-22 (the Minecraft build guide, server/build_guide.py) the
Ombri view also carries the Build pane (client/gui/build_pane.py): not a
page - an overlay over the stack on which the orb morphs into the pane when
a guide opens ("quero construir uma casa", the "Construir" button) and back
when it closes. The pane keeps the guide's state in memory; the window
re-emits it (`guide_state_changed`) for main.py's next turn_start.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget,
)

from client.gui import indicator, motion
from client.gui.build_pane import GuideMorph
from client.gui.chat_view import CHAT_W, ChatView
from client.gui.fonts import HEADING_FAMILY, ensure_fonts_loaded
from client.gui.orb import HALO_STATES, breathing_scale, draw_sphere, draw_thinking_halo, sphere_image, state_color
from client.gui.settings_panel import SettingsPanel
from client.gui.shadow import HEAD_CY, HEAD_RX, HEAD_RY
from client.gui.speaking import make_design
from client.gui.ui_texts import UI, questions, ui
from client.gui.widgets import (  # noqa: F401 - the palette and helpers lived here until 2026-11-21; still exported
    ACCENT, BAR, BG, CARD, CHIP_TEXT, CONTROL, OK_GREEN, QSS, RADIUS, TEXT, TEXT_2, TEXT_3, WARN_AMBER, WINDOW_H,
    WINDOW_W, _card, _label, line_icon,
)


class OrbWidget(QWidget):
    """The breathing sphere; `ring` adds the main view's thin ring. `halo` is the
    radius of the ring of light while Ombri thinks (`orb.draw_thinking_halo()`), eased
    in and out over ~0.3 s; with a `ring` and no `halo` it sits just outside the ring.
    While Ombri talks, the look comes from the library in client/gui/speaking/."""

    def __init__(self, box: int, radius: float, ring: float | None = None, glow: float = 1.0,
                 halo: float | None = None) -> None:
        super().__init__()
        self.setFixedSize(box, box)
        self._radius, self._ring, self._glow = radius, ring, glow
        self.state = "idle"
        self._halo_radius = halo or (ring + 4 if ring else None)
        self._halo = 0.0
        self._speaking = make_design()
        self._eyes = indicator.custom()  # eyes or shadow; client/gui/indicator.py: "orb" brings the sphere back
        self._level = 0.0  # the voice's loudness for the eyes' bounce, decays between feeds
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        # The default timer rounds 33 ms up to ~47 on Windows: 21 fps, measured 2026-11-06.
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self.update)

    def set_state(self, state: str) -> None:
        self.state = state
        self.update()

    def shape(self) -> tuple[QPointF, float, float]:
        """Where Ombri's body is inside this widget - (centre, rx, ry) of the
        shadow head's ellipse (or the sphere) - so the Build pane's drop can
        start exactly there (client/gui/build_pane.py)."""
        c = QPointF(self.width() / 2, self.height() / 2)
        if indicator.STYLE == "shadow":
            s = self._radius * 2 * 0.9  # draw_shadow(): s = box * 0.9, the head at (0, HEAD_CY * s)
            return QPointF(c.x(), c.y() + HEAD_CY * s), HEAD_RX * s, HEAD_RY * s
        return c, float(self._radius), float(self._radius)

    def feed_voice(self, level: float) -> None:
        if self.isVisible():
            self._speaking.feed_level(level, time.monotonic() - self._t0)
            self._level = max(self._level, level)

    def showEvent(self, event) -> None:  # noqa: N802 - animate only while visible
        self._timer.start(33)
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        t = time.monotonic() - self._t0
        c = QPointF(self.width() / 2, self.height() / 2)

        if self._eyes:
            self._level *= 0.82  # ~10 feeds a second against ~30 frames: a short tail
            # The eyes follow the cursor, anywhere on screen (owner, 2026-11-19):
            # full turn ~250 px away, easing off closer in.
            v = self.mapFromGlobal(QCursor.pos()) - c.toPoint()
            dist = max(1.0, (v.x() ** 2 + v.y() ** 2) ** 0.5)
            reach = min(1.0, dist / 250.0) / dist
            indicator.draw(p, c, self._radius * 2, self.state, t, level=self._level,
                           look=(v.x() * reach, v.y() * reach))
            p.end()
            return

        def draw_orb(q: QPainter) -> None:
            draw_sphere(q, c, self._radius * breathing_scale(self.state, t), state_color(self.state),
                        glow=self._glow, ring_radius=self._ring)
            if self._halo_radius:
                self._halo += ((1.0 if self.state in HALO_STATES else 0.0) - self._halo) * 0.12
                draw_thinking_halo(q, c, self._halo_radius, state_color("pensando"), t, self._halo)

        self._speaking.paint(p, c, self._halo_radius, t, self.state, draw_orb, self.width(), self.devicePixelRatioF())
        p.end()


class TitleBar(QWidget):
    hide_requested = Signal()

    def __init__(self, window: QWidget) -> None:
        super().__init__()
        self.setObjectName("titlebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFixedHeight(44)
        self._window = window
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 0, 6, 0)
        row.setSpacing(10)
        dot = QLabel()
        dot.setPixmap(QPixmap.fromImage(indicator.icon_image(28)).scaled(
            14, 14, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        row.addWidget(dot)
        name = _label("Ombri", size=15, weight=600, heading=True)
        f = name.font()
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
        name.setFont(f)
        row.addWidget(name)
        self.beta = _label("beta", size=12, color=TEXT_3)
        row.addWidget(self.beta)
        row.addStretch(1)
        self.min_btn = self._button("minus")
        self.close_btn = self._button("close")
        row.addWidget(self.min_btn)
        row.addWidget(self.close_btn)

    def _button(self, icon: str) -> QPushButton:
        b = QPushButton()
        b.setObjectName("winbtn")
        b.setFixedSize(40, 32)
        b.setIcon(line_icon(icon, TEXT_2, 14))
        b.setIconSize(QSize(14, 14))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(self.hide_requested.emit)
        return b

    def mousePressEvent(self, event) -> None:  # noqa: N802 - drag the frameless window by its title bar
        if event.button() == Qt.MouseButton.LeftButton and self._window.windowHandle():
            self._window.windowHandle().startSystemMove()


def _fmt_date(iso: str, lang: str) -> str:
    """2026-10-31 -> 31/10/2026 (pt, es) / 10/31/2026 (en); anything else as is."""
    parts = (iso or "").split("-")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return iso or ""
    y, m, d = parts
    return f"{m}/{d}/{y}" if lang == "en" else f"{d}/{m}/{y}"  # pt and es: day first


class NoteCard(QFrame):
    """One note on the "Minhas notas" page: date · game, the text, and a
    delete that asks first (docs/player_notes.md, rule 4: delete only)."""
    delete_requested = Signal(str)

    def __init__(self, note: dict, lang: str) -> None:
        super().__init__()
        self.setObjectName("card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.note_id = str(note.get("id", ""))
        self.lang = lang
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 12, 14, 10)
        col.setSpacing(6)
        head = [_fmt_date(str(note.get("created", "")), lang), str(note.get("game", ""))]
        if note.get("source") == "recap":
            head.append(ui(lang, "notes_recap"))
        caps = _label(" · ".join(h for h in head if h), name="caps")
        col.addWidget(caps)
        text = _label(str(note.get("note", "")), size=14)
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(text)
        self.row = QHBoxLayout()
        self.row.setSpacing(8)
        col.addLayout(self.row)
        self._idle()

    def _clear_row(self) -> None:
        while self.row.count():
            widget = self.row.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)  # gone now, not only when the event loop gets to deleteLater
                widget.deleteLater()

    def _button(self, key: str, name: str = "small") -> QPushButton:
        b = QPushButton(ui(self.lang, key))
        b.setObjectName(name)
        b.setFixedHeight(30)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    def _idle(self) -> None:
        self._clear_row()
        self.row.addStretch(1)
        delete = self._button("notes_delete")
        delete.clicked.connect(self._ask)
        self.row.addWidget(delete)

    def _ask(self) -> None:
        self._clear_row()
        question = _label(ui(self.lang, "notes_confirm"), name="sub")
        self.row.addWidget(question, 1)
        yes = self._button("notes_yes", "danger")
        no = self._button("notes_no")
        yes.clicked.connect(self._confirm)
        no.clicked.connect(self._idle)
        self.row.addWidget(yes)
        self.row.addWidget(no)

    def _confirm(self) -> None:
        self._clear_row()
        self.row.addStretch(1)
        self.row.addWidget(_label(ui(self.lang, "notes_deleting"), name="sub"))
        self.delete_requested.emit(self.note_id)


VIEWS = ("ombri", "notes", "settings")  # the home column; the chat opens beside it


class _ChatClip(QWidget):
    """The strip right of the home column. The chat inside keeps its full
    width while the window widens, so nothing reflows mid-animation - the
    strip just reveals more of it."""

    def __init__(self, chat: QWidget) -> None:
        super().__init__()
        self.chat = chat
        chat.setParent(self)
        chat.setFixedWidth(CHAT_W)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self.chat.setGeometry(0, 0, CHAT_W, self.height())
        super().resizeEvent(event)


class MainWindow(QWidget):
    hidden_to_tray = Signal()
    notes_requested = Signal()  # "Minhas notas" opened or refreshed: fetch the list
    note_delete_requested = Signal(str)  # the player confirmed a delete
    game_pick_requested = Signal()  # "Trocar" on the game card: open the game picker
    chat_send_requested = Signal(str, bool)  # a typed message (text, remember) -> main.py
    chat_mic_requested = Signal()  # the chat's mic button: F8
    chat_confirm_answered = Signal(bool)  # a typed note's "Sim, guardar" / "Não"
    chat_toggled = Signal(bool)  # the chat opened / closed (ui_process loads the links on open)
    # The Build pane (client/gui/build_pane.py): where it is now (id, layer, listing),
    # a build clicked in its list, the "Construir" button (fetch the list).
    guide_state_changed = Signal(str, int, bool)
    guide_open_requested = Signal(str)
    guide_list_requested = Signal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        ensure_fonts_loaded()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Ombri")
        self.setWindowIcon(indicator.app_icon())
        self.setFixedSize(WINDOW_W, WINDOW_H)
        self.setStyleSheet(QSS)

        self.lang = "pt"
        self.keys = {"ask": "F8", "note": "F6", "pad": None}
        self.voice_label = ""
        self.state = "idle"
        self.conn = "connecting"
        self.game: str | None = None
        self.usage: tuple[int, int] | None = None
        self.notes_enabled = False  # the server listed "notes" (features)
        self.guide_enabled = False  # the server listed "build_guide" (features): the "Construir" button
        self.view = "ombri"  # one of VIEWS: what the home column shows
        self.chat_open = False  # the chat beside the home column (remembered across hide-to-tray)
        self._chat_w = 0  # how much of the chat is revealed, 0..CHAT_W (animated)
        # a spring, not a curve: opening and closing carry momentum and can reverse mid-way
        self._chat_spring = motion.Spring(self, 0.0, self._set_chat_w, *motion.SPRING_LAYOUT)
        motion.install_ripples()  # every button: a drop's ring where it's pressed
        self.notes: list[dict] | None = None  # None = loading
        self.notes_error = ""

        root = QWidget(self)
        self.root = root
        root.setObjectName("root")
        root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        root.setGeometry(0, 0, WINDOW_W, WINDOW_H)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.titlebar = TitleBar(self)
        self.titlebar.hide_requested.connect(self._hide_to_tray)
        outer.addWidget(self.titlebar)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer.addLayout(body, 1)
        home = QWidget()
        home.setFixedWidth(WINDOW_W)
        h = QVBoxLayout(home)
        h.setContentsMargins(0, 10, 0, 0)
        h.setSpacing(0)
        self.stack = QStackedWidget()
        h.addWidget(self.stack, 1)
        body.addWidget(home)
        self.stack.addWidget(self._build_waiting())
        self.stack.addWidget(self._build_status())
        self.stack.addWidget(self._build_notes())
        self.settings = SettingsPanel()
        self.stack.addWidget(self._build_settings_page())
        self.chat = ChatView()
        self.chat.send_requested.connect(self.chat_send_requested.emit)
        self.chat.mic_requested.connect(self.chat_mic_requested.emit)
        self.settings.advanced.links_cleared.connect(lambda: self.chat.set_links([]))
        self.chat.game_requested.connect(self.game_pick_requested.emit)
        self.chat.confirm_answered.connect(self.chat_confirm_answered.emit)
        self.chat.close_requested.connect(self.close_chat)
        self.chat_clip = _ChatClip(self.chat)
        body.addWidget(self.chat_clip, 1)
        # The Build pane: an overlay the size of the stack, over the Ombri view only.
        self.guide = GuideMorph(root, OrbWidget(56, 26))
        self.guide.state_changed.connect(self.guide_state_changed.emit)
        self.guide.open_requested.connect(self.guide_open_requested.emit)
        self.guide.phase_changed.connect(lambda _phase: self._refresh())
        self.stack.installEventFilter(self)  # keep the overlay on the stack's geometry
        for orb in (self.s_orb, self.w_orb):
            # Hidden while the glass is out (the drop IS the orb: one shape, never two),
            # the room kept so nothing under it moves.
            policy = orb.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            orb.setSizePolicy(policy)
        self._refresh()

    @property
    def showing_notes(self) -> bool:
        return self.view == "notes"

    # ------------------------------------------------------------------ views
    def _build_settings_page(self) -> QWidget:
        """The settings panel under a "Voltar" row (no nav to leave it by)."""
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(20, 0, 20, 0)
        row.addStretch(1)
        self.set_back = QPushButton()
        self.set_back.setObjectName("btn")
        self.set_back.setFixedHeight(36)
        self.set_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_back.clicked.connect(self.open_ombri)
        row.addWidget(self.set_back)
        col.addLayout(row)
        col.addWidget(self.settings, 1)
        return page

    def _conn_chip(self) -> tuple[QFrame, QLabel, QLabel]:
        chip = QFrame()
        chip.setObjectName("chip")
        chip.setFixedHeight(28)
        row = QHBoxLayout(chip)
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(8)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        text = _label(size=13, color=CHIP_TEXT)
        row.addWidget(dot)
        row.addWidget(text)
        return chip, dot, text

    def _build_waiting(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(24, 0, 24, 22)
        col.setSpacing(16)
        # The widget is larger than the sphere so the glow fades out inside
        # it (a smaller box clipped the glow into a visible square).
        # (box, half the character's size): the shadow is the app's face, so it gets more room than the eyes.
        size = {"shadow": (140, 64), "eyes": (96, 36)}.get(indicator.STYLE)
        self.w_orb = OrbWidget(*size) if size else OrbWidget(216, 56, glow=0.8, halo=76)
        col.addWidget(self.w_orb, 0, Qt.AlignmentFlag.AlignHCenter)
        self.w_title = _label(size=26, weight=600, heading=True)
        self.w_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(self.w_title)
        self.w_body = _label(size=15, color=TEXT_2)
        self.w_body.setWordWrap(True)
        self.w_body.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.w_body.setFixedWidth(330)
        col.addWidget(self.w_body, 0, Qt.AlignmentFlag.AlignHCenter)
        chip, self.w_dot, self.w_conn = self._conn_chip()
        col.addWidget(chip, 0, Qt.AlignmentFlag.AlignHCenter)

        tips = _card()
        tcol = QVBoxLayout(tips)
        tcol.setContentsMargins(18, 18, 18, 18)
        tcol.setSpacing(14)
        self.w_tips_caps = _label(name="caps")
        tcol.addWidget(self.w_tips_caps)
        self.w_tips = []
        for icon in ("screen", "shield", "mic"):
            row = QHBoxLayout()
            row.setSpacing(12)
            ic = QLabel()
            ic.setPixmap(line_icon(icon, ACCENT, 20).pixmap(20, 20))
            ic.setAlignment(Qt.AlignmentFlag.AlignTop)
            text = QLabel()
            text.setWordWrap(True)
            text.setTextFormat(Qt.TextFormat.RichText)
            text.setStyleSheet("font-size: 14px;")
            row.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(text, 1)
            tcol.addLayout(row)
            self.w_tips.append(text)
        col.addWidget(tips)
        col.addStretch(1)
        self.w_chat = QPushButton()  # the chat works before a game too (it asks which one)
        self.w_chat.setObjectName("primary")
        self.w_chat.setFixedHeight(44)
        self.w_chat.setCursor(Qt.CursorShape.PointingHandCursor)
        self.w_chat.setIcon(line_icon("chat", "#14161C"))
        self.w_chat.clicked.connect(self.toggle_chat)
        col.addWidget(self.w_chat, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(8)
        self.w_settings = QPushButton()
        self.w_settings.setObjectName("btn")
        self.w_settings.setFixedHeight(44)
        self.w_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.w_settings.clicked.connect(self.open_settings)
        col.addWidget(self.w_settings, 0, Qt.AlignmentFlag.AlignHCenter)
        return page

    def _build_status(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(20, 0, 20, 0)
        col.setSpacing(14)

        hero = QVBoxLayout()
        hero.setSpacing(8)
        # The glow reaches 124 px at the top of the pulse; a smaller box cuts it into a square.
        eyes = indicator.custom()
        size = {"shadow": (132, 60), "eyes": (104, 40)}.get(indicator.STYLE)
        self.s_orb = OrbWidget(*size) if size else OrbWidget(256, 66, ring=88, glow=0.8)
        hero.addWidget(self.s_orb, 0, Qt.AlignmentFlag.AlignHCenter)
        self.s_state = _label(size=22, weight=600, heading=True)
        self.s_state.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        if not eyes:
            hero.addSpacing(-34)  # the sphere's glow box is taller than the sphere
        hero.addWidget(self.s_state)
        self.s_hint = _label(size=14, color=TEXT_2)
        self.s_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        hero.addWidget(self.s_hint)
        col.addLayout(hero)

        chip, self.s_dot, self.s_conn = self._conn_chip()
        col.addWidget(chip, 0, Qt.AlignmentFlag.AlignHCenter)

        game = _card()
        g = QVBoxLayout(game)
        g.setContentsMargins(16, 14, 16, 14)
        g.setSpacing(4)
        self.s_game_caps = _label(name="caps")
        self.s_game = _label(size=16, weight=600)
        self.s_game_sub = _label(name="sub")
        # "Trocar" (owner, 2026-11-12): pick or type the game, no restart needed.
        self.s_game_change = QPushButton()
        self.s_game_change.setObjectName("small")
        self.s_game_change.setFixedHeight(28)
        self.s_game_change.setCursor(Qt.CursorShape.PointingHandCursor)
        self.s_game_change.clicked.connect(self.game_pick_requested.emit)
        caps_row = QHBoxLayout()
        caps_row.addWidget(self.s_game_caps)
        caps_row.addStretch(1)
        caps_row.addWidget(self.s_game_change)
        g.addLayout(caps_row)
        for w in (self.s_game, self.s_game_sub):
            g.addWidget(w)
        col.addWidget(game)

        usage = _card()
        u = QVBoxLayout(usage)
        u.setContentsMargins(16, 14, 16, 14)
        u.setSpacing(10)
        top = QHBoxLayout()
        self.s_usage_caps = _label(name="caps")
        self.s_usage = QLabel()
        self.s_usage.setTextFormat(Qt.TextFormat.RichText)
        self.s_usage.setStyleSheet("font-size: 14px;")
        top.addWidget(self.s_usage_caps)
        top.addStretch(1)
        top.addWidget(self.s_usage)
        u.addLayout(top)
        self.s_bar = motion.WaterBar(ACCENT, CONTROL)  # fills like water (2026-11-23)
        self.s_bar.setFixedHeight(8)
        self.s_bar.setTextVisible(False)
        self.s_bar.setRange(0, 100)
        u.addWidget(self.s_bar)
        col.addWidget(usage)

        self.s_keys = QGridLayout()
        self.s_keys.setSpacing(8)
        col.addLayout(self.s_keys)

        # "Conversar com o Ombri" (docs/chat_redesign.md): the chat opens beside this column.
        self.s_chat = QPushButton()
        self.s_chat.setObjectName("primary")
        self.s_chat.setFixedHeight(42)
        self.s_chat.setCursor(Qt.CursorShape.PointingHandCursor)
        self.s_chat.setIcon(line_icon("chat", "#14161C"))
        self.s_chat.clicked.connect(self.toggle_chat)
        col.addWidget(self.s_chat)

        self.s_notes = QPushButton()
        self.s_notes.setObjectName("btn")
        self.s_notes.setFixedHeight(40)
        self.s_notes.setCursor(Qt.CursorShape.PointingHandCursor)
        self.s_notes.setIcon(line_icon("note", TEXT))
        self.s_notes.clicked.connect(self.open_notes)
        self.s_notes.hide()  # until the server says it has the notes endpoints
        # Notes and Build share one row: with the chat button, three stacked rows
        # squeezed the mascot under its own "Pronto" (2026-11-23 screenshot).
        extras = QHBoxLayout()
        extras.setSpacing(8)
        extras.addWidget(self.s_notes, 1)

        # "Construir" (the build guide, 2026-11-22): the list of builds without a
        # word said - the pane then works by clicks and keys alone.
        self.s_build = QPushButton()
        self.s_build.setObjectName("btn")
        self.s_build.setFixedHeight(40)
        self.s_build.setCursor(Qt.CursorShape.PointingHandCursor)
        self.s_build.setIcon(line_icon("cube", TEXT))
        self.s_build.clicked.connect(self.guide_list_requested.emit)
        self.s_build.hide()  # until the server says it has the build guide
        extras.addWidget(self.s_build, 1)
        col.addLayout(extras)

        voice = _card()
        v = QHBoxLayout(voice)
        v.setContentsMargins(16, 12, 12, 12)
        v.setSpacing(12)
        vcol = QVBoxLayout()
        vcol.setSpacing(2)
        self.s_voice_caps = _label(name="caps")
        self.s_voice = _label(size=15, weight=600)
        vcol.addWidget(self.s_voice_caps)
        vcol.addWidget(self.s_voice)
        v.addLayout(vcol, 1)
        self.s_settings = QPushButton()
        self.s_settings.setObjectName("btn")
        self.s_settings.setFixedHeight(40)
        self.s_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.s_settings.clicked.connect(self.open_settings)
        v.addWidget(self.s_settings)
        col.addWidget(voice)

        col.addStretch(1)
        self.s_footer = _label(size=12, color=TEXT_3)
        self.s_footer.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.s_footer.setWordWrap(True)
        self.s_footer.setContentsMargins(0, 10, 0, 18)
        col.addWidget(self.s_footer)
        return page

    def _build_notes(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(20, 6, 20, 0)
        col.setSpacing(10)
        head = QHBoxLayout()
        self.n_title = _label(size=22, weight=600, heading=True)
        head.addWidget(self.n_title, 1)
        self.n_back = QPushButton()
        self.n_back.setObjectName("btn")
        self.n_back.setFixedHeight(36)
        self.n_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.n_back.clicked.connect(self.close_notes)
        head.addWidget(self.n_back)
        col.addLayout(head)
        self.n_status = _label(name="sub")
        self.n_status.setWordWrap(True)
        col.addWidget(self.n_status)
        self.n_scroll = QScrollArea()
        self.n_scroll.setWidgetResizable(True)
        self.n_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.n_list_host = QWidget()
        self.n_list = QVBoxLayout(self.n_list_host)
        self.n_list.setContentsMargins(0, 0, 6, 12)
        self.n_list.setSpacing(10)
        self.n_list.addStretch(1)
        self.n_scroll.setWidget(self.n_list_host)
        col.addWidget(self.n_scroll, 1)
        return page

    def _render_notes(self) -> None:
        L = self.lang
        self.n_title.setText(ui(L, "notes_title"))
        self.n_back.setText(ui(L, "notes_back"))
        while self.n_list.count() > 1:  # keep the trailing stretch
            widget = self.n_list.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        if self.notes_error:
            self.n_status.setText(ui(L, "notes_error"))
            return
        if self.notes is None:
            self.n_status.setText(ui(L, "notes_loading"))
            return
        if not self.notes:
            self.n_status.setText(ui(L, "notes_empty", note=self.keys["note"]))
            return
        self.n_status.setText(ui(L, "notes_count", n=len(self.notes)))
        for i, note in enumerate(self.notes):
            card = NoteCard(note, L)
            card.delete_requested.connect(self.note_delete_requested.emit)
            self.n_list.insertWidget(i, card)

    def _rebuild_keys(self) -> None:
        while self.s_keys.count():
            item = self.s_keys.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        tiles = [(self.keys["ask"], "key_ask"), (self.keys["note"], "key_note")]
        if self.keys.get("pad"):
            tiles.append((self.keys["pad"], "key_pad"))
        for i, (cap, label) in enumerate(tiles):
            tile = _card()
            t = QVBoxLayout(tile)
            t.setContentsMargins(4, 10, 4, 10)
            t.setSpacing(6)
            key = _label(cap, name="keycap")
            key.setAlignment(Qt.AlignmentFlag.AlignCenter)
            t.addWidget(key, 0, Qt.AlignmentFlag.AlignHCenter)
            name = _label(ui(self.lang, label), size=12, color=TEXT_2)
            name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            t.addWidget(name)
            self.s_keys.addWidget(tile, 0, i)

    # ------------------------------------------------------------------ updates
    def _refresh(self) -> None:
        L = self.lang
        kw = {"ask": self.keys["ask"], "note": self.keys["note"]}
        if indicator.custom():  # one colour with the character: coral while busy, grey at rest
            color = TEXT_2 if self.state in ("idle", "pausado") else ACCENT
        else:
            color = state_color(self.state).name()
        self.s_orb.set_state(self.state)
        self.w_orb.set_state(self.state)
        self.s_state.setText(ui(L, f"state_{self.state}"))
        self.s_state.setStyleSheet(f'font-family: "{HEADING_FAMILY}"; font-size: 22px; font-weight: 600; color: {color};')
        self.s_hint.setText(ui(L, f"hint_{self.state}", **kw))

        dot = {"ok": OK_GREEN, "offline": WARN_AMBER}.get(self.conn, TEXT_3)
        for d, lab in ((self.s_dot, self.s_conn), (self.w_dot, self.w_conn)):
            d.setStyleSheet(f"background: {dot}; border-radius: 4px;")
            lab.setText(ui(L, f"conn_{self.conn}"))

        self.s_game_caps.setText(ui(L, "game"))
        self.s_game_change.setText(ui(L, "game_change"))
        self.s_game.setText(self.game or ui(L, "game_none"))
        self.s_game_sub.setText(ui(L, "game_sub") if self.game else ui(L, "game_none_sub"))

        self.s_usage_caps.setText(ui(L, "usage"))
        if self.usage:
            count, cap = self.usage
            left = max(0, cap - count)
            self.s_usage.setText(f"<b>~{questions(left)}</b> <span style='color:{TEXT_2}'>"
                                 f"{ui(L, 'usage_left', cap=questions(cap))}</span>")
            self.s_bar.setValue(round(100 * left / cap) if cap else 0)
        else:
            self.s_usage.setText(f"<span style='color:{TEXT_2}'>—</span>")
            self.s_bar.setValue(0)

        self.s_voice_caps.setText(ui(L, "voice"))
        self.s_voice.setText(self.voice_label)
        self.s_settings.setText(ui(L, "settings"))
        self.s_notes.setText(ui(L, "notes_button"))
        self.s_notes.setVisible(self.notes_enabled)
        self.s_build.setText(ui(L, "guide_button"))
        self.s_build.setVisible(self.guide_enabled)
        self.w_settings.setText(ui(L, "settings"))
        self.s_footer.setText(ui(L, "footer"))

        self.w_title.setText(ui(L, "wait_title"))
        self.w_body.setText(ui(L, "wait_body", **kw))
        self.w_body.setFixedHeight(self.w_body.heightForWidth(330))
        self.w_tips_caps.setText(ui(L, "wait_tips"))
        for lab, key in zip(self.w_tips, ("tip_window", "tip_admin", "tip_mic")):
            lab.setText(f"<span style='font-weight:600'>{ui(L, key + '_b')}</span> "
                        f"<span style='color:{TEXT_2}'>{ui(L, key)}</span>")

        for b in (self.s_chat, self.w_chat):
            b.setText(ui(L, "chat_is_open" if self.chat_open else "chat_open"))
        self.set_back.setText(ui(L, "notes_back"))
        if self.view not in VIEWS or (self.view == "notes" and not self.notes_enabled):
            self.view = "ombri"
        index = {"notes": 2, "settings": 3}.get(self.view, 1 if self.game else 0)
        self.stack.setCurrentIndex(index)
        self.guide.small_orb.set_state(self.state)
        self.guide.set_game(self.game)
        self.guide.setVisible(self.view == "ombri" and self.guide.active)
        if self.guide.isVisible():
            self.guide.raise_()
        for orb in (self.s_orb, self.w_orb):
            orb.setVisible(not self.guide.active)  # the drop took its place

    def set_state(self, state: str) -> None:
        self.state = state
        self.chat.set_state(state)
        self._refresh()

    def set_connection(self, conn: str) -> None:
        self.conn = conn
        self.chat.set_connection(conn)
        if conn == "offline":
            self.chat.turn_failed()
        self._refresh()

    def set_game(self, name: str | None) -> None:
        if name:  # sticky, like the client's own active_game
            self.game = name
            self.chat.set_game(name)
            self._refresh()

    def set_voice_level(self, level: float) -> None:
        self.s_orb.feed_voice(level)
        self.w_orb.feed_voice(level)
        self.guide.small_orb.feed_voice(level)
        self.chat.set_voice_level(level)

    def set_usage(self, count: int, cap: int) -> None:
        self.usage = (count, cap)
        self._refresh()

    def set_info(self, lang: str, voice_label: str, keys: dict) -> None:
        self.lang = lang if lang in UI else "pt"
        self.voice_label = voice_label
        self.keys = {"ask": keys.get("ask", "F8"), "note": keys.get("note", "F6"), "pad": keys.get("pad")}
        self.chat.set_language(self.lang, self.keys)
        self.guide.set_language(self.lang, self.keys)
        self._rebuild_keys()
        self._refresh()

    # ------------------------------------------------------------------ notes
    def set_features(self, features: list) -> None:
        names = {str(f) for f in features}
        self.notes_enabled = "notes" in names
        self.guide_enabled = "build_guide" in names
        self._refresh()  # drops the notes view when the feature is gone

    def set_notes(self, items: list[dict], error: str = "") -> None:
        self.notes = [n for n in items if isinstance(n, dict)]
        self.notes_error = error
        self._render_notes()

    def open_notes(self) -> None:
        """The button or the nav: show the page in its loading state and ask main.py."""
        if not self.notes_enabled:
            self._refresh()  # the nav button stays on the current view
            return
        self.view = "notes"
        self.notes, self.notes_error = None, ""
        self._render_notes()
        self._refresh()
        self.notes_requested.emit()

    def close_notes(self) -> None:
        self.open_ombri()

    # ------------------------------------------------------------------ views
    def open_ombri(self) -> None:
        self.view = "ombri"
        self._refresh()

    # ------------------------------------------------------------------ the chat beside the home column
    def toggle_chat(self) -> None:
        if self.chat_open:
            self.close_chat()
        else:
            self.open_chat()

    def open_chat(self, tab: int | None = None) -> None:
        """The window widens to the right (~300 ms, eased) and the chat fades
        in beside the home column, which doesn't move. If the screen's right
        edge is in the way, the window slides left just enough."""
        if tab is not None:
            self.chat.open_tab(tab)
        if self.chat_open:
            return
        self.chat_open = True
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None and self.isVisible():
            overflow = self.x() + WINDOW_W + CHAT_W - screen.right()
            if overflow > 0:
                self.move(max(screen.left(), self.x() - overflow), self.y())
        motion.fade_in(self.chat, ms=motion.LAYOUT, rise=0)
        self._chat_spring.to(float(CHAT_W))
        self._refresh()
        self.chat_toggled.emit(True)
        QTimer.singleShot(motion.LAYOUT if motion.enabled() else 0, self, self.chat.input.edit.setFocus)

    def close_chat(self) -> None:
        if not self.chat_open:
            return
        self.chat_open = False
        self._chat_spring.to(0.0)
        self._refresh()
        self.chat_toggled.emit(False)

    def _set_chat_w(self, value: float) -> None:
        self._chat_w = int(round(max(0.0, min(float(CHAT_W), value))))
        w = WINDOW_W + self._chat_w
        self.setFixedSize(w, WINDOW_H)
        self.root.setGeometry(0, 0, w, WINDOW_H)

    def open_settings(self) -> None:
        """The "Configurações" buttons and the tray: the panel, with the
        values the file has right now (edits in progress are kept when the
        panel is already the view)."""
        if self.view != "settings":
            self.settings.reload()
        self.view = "settings"
        self._refresh()

    # ------------------------------------------------------------------ the Build pane
    def _orb_origin(self) -> tuple[QPointF, float, float]:
        """The visible orb's body in the overlay's coordinates: where the drop starts."""
        orb = self.s_orb if self.stack.currentIndex() == 1 else self.w_orb
        centre, rx, ry = orb.shape()
        root = self.guide.parentWidget()
        return self.guide.mapFrom(root, orb.mapTo(root, centre.toPoint())).toPointF(), rx, ry

    def guide_message(self, msg: dict) -> None:
        """A `guide` message from main.py (the server's, or a REST click-open):
        the pane applies it. The morph plays only when the Ombri view is on
        screen; elsewhere the pane settles and shows when the view comes back."""
        on_screen = self.view == "ombri" and self.isVisible()
        self.guide.apply(msg, self._orb_origin() if on_screen else None, animate=on_screen)
        self._refresh()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self.stack and event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            # The stack sits inside the home column now: map it into the root.
            top_left = self.stack.mapTo(self.root, QPoint(0, 0))
            self.guide.setGeometry(QRect(top_left, self.stack.size()))
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - the pane's keys: arrows, Home/End, 1-9, Esc
        if self.view == "ombri" and self.guide.active and self.guide.handle_key(event):
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------ chat
    def chat_question(self, text: str, game: str | None = None) -> None:
        self.chat.add_question(text, game)

    def chat_answer(self, piece: str) -> None:
        self.chat.append_answer(piece)

    def chat_cards(self, cards: list) -> None:
        self.chat.set_cards(cards)

    # ------------------------------------------------------------------ window
    def show_and_raise(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _hide_to_tray(self) -> None:
        self.hide()
        if self.view != "ombri":
            self.view = "ombri"  # reopens on the status page (the chat stays as it was)
            self._refresh()
        self.hidden_to_tray.emit()

    def closeEvent(self, event) -> None:  # noqa: N802 - Alt+F4 also only hides
        event.ignore()
        self._hide_to_tray()
