"""client/gui/main_window.py — Ombri's desktop window (stage 1, 2026-09-29).

Built to the approved mockup (the "Janela principal" and "Sem jogo aberto"
screens): a dark, frameless, rounded window with its own title bar. Before a
game is detected it shows the "Abra um jogo" view; from the first detected
game on, the status view: the orb and its state, the connection, the game,
questions left today, the hotkeys, the voice. Minimize and close both hide
it to the tray - Ombri keeps running.

Pure view: it never talks to the voice loop itself. ui_process.py feeds it
(set_state, set_game, ...) and relays its buttons as commands.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout,
    QWidget,
)

from client.gui.fonts import BODY_FAMILY, HEADING_FAMILY, ensure_fonts_loaded
from client.gui.orb import HALO_STATES, breathing_scale, draw_sphere, draw_thinking_halo, sphere_image, state_color
from client.gui.speaking import make_design
from client.gui.ui_texts import UI, questions, ui

WINDOW_W, WINDOW_H = 440, 780
RADIUS = 12

BG = "#111318"
BAR = "#0C0E12"
CARD = "#1A1D24"
CONTROL = "#2A2E38"
TEXT = "#E7E9ED"
TEXT_2 = "#A3A8B3"
TEXT_3 = "#8A919E"
CHIP_TEXT = "#C9CDD4"
ACCENT = "#FF6B4A"
OK_GREEN = "#34D399"
WARN_AMBER = "#F5B740"

QSS = f"""
QWidget#root {{ background: {BG}; border-radius: {RADIUS}px; }}
QWidget#titlebar {{ background: {BAR}; border-top-left-radius: {RADIUS}px; border-top-right-radius: {RADIUS}px; }}
QLabel {{ color: {TEXT}; font-family: "{BODY_FAMILY}"; background: transparent; }}
QFrame#card {{ background: {CARD}; border-radius: 10px; }}
QFrame#chip {{ background: {CARD}; border-radius: 13px; }}
QLabel#caps {{ color: {TEXT_3}; font-size: 11px; font-weight: 700; }}
QLabel#sub {{ color: {TEXT_2}; font-size: 13px; }}
QLabel#keycap {{ background: {CONTROL}; border-radius: 6px; border-bottom: 2px solid #0B0C10;
                 font-family: "{HEADING_FAMILY}"; font-weight: 600; font-size: 14px; padding: 3px 10px; }}
QPushButton#winbtn {{ background: transparent; border: none; border-radius: 6px; }}
QPushButton#winbtn:hover {{ background: #1F232B; }}
QPushButton#btn {{ background: {CONTROL}; color: {TEXT}; border: none; border-radius: 8px;
                   font-family: "{BODY_FAMILY}"; font-size: 14px; font-weight: 600; padding: 0 16px; }}
QPushButton#btn:hover {{ background: #2E3340; }}
QPushButton#small {{ background: {CONTROL}; color: {TEXT}; border: none; border-radius: 6px;
                     font-family: "{BODY_FAMILY}"; font-size: 13px; font-weight: 600; padding: 0 12px; }}
QPushButton#small:hover {{ background: #2E3340; }}
QPushButton#danger {{ background: #3A1F19; color: #FF8A6E; border: none; border-radius: 6px;
                      font-family: "{BODY_FAMILY}"; font-size: 13px; font-weight: 600; padding: 0 12px; }}
QPushButton#danger:hover {{ background: #4A261E; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {CONTROL}; border-radius: 3px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QProgressBar {{ background: {CONTROL}; border: none; border-radius: 3px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
"""


def _label(text: str = "", name: str | None = None, size: int | None = None, weight: int | None = None,
           color: str | None = None, heading: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    style = []
    if heading:
        style.append(f'font-family: "{HEADING_FAMILY}"')
    if size:
        style.append(f"font-size: {size}px")
    if weight:
        style.append(f"font-weight: {weight}")
    if color:
        style.append(f"color: {color}")
    if style:
        lab.setStyleSheet("; ".join(style) + ";")
    if name == "caps":
        f = lab.font()
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.9)
        lab.setFont(f)
    return lab


def _card() -> QFrame:
    frame = QFrame()
    frame.setObjectName("card")
    return frame


def line_icon(kind: str, color: str = TEXT, size: int = 16) -> QIcon:
    """Small stroke icons (title bar, tips, tray menu) - the mockup's inline
    SVGs, redrawn with QPainter on a 16-unit grid."""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 16, size / 16)
    pen = QPen(QColor(color), 1.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "minus":
        p.drawLine(QPointF(3, 8), QPointF(13, 8))
    elif kind == "close":
        p.drawLine(QPointF(4, 4), QPointF(12, 12))
        p.drawLine(QPointF(12, 4), QPointF(4, 12))
    elif kind == "window":
        p.drawRoundedRect(QRectF(2, 3, 12, 10), 1.5, 1.5)
        p.drawLine(QPointF(2, 6), QPointF(14, 6))
    elif kind == "pause":
        p.drawLine(QPointF(5.5, 3), QPointF(5.5, 13))
        p.drawLine(QPointF(10.5, 3), QPointF(10.5, 13))
    elif kind == "play":
        path = QPainterPath(QPointF(5, 3))
        path.lineTo(13, 8)
        path.lineTo(5, 13)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "gear":
        p.drawEllipse(QPointF(8, 8), 2.2, 2.2)
        for a, b in (((8, 1.8), (8, 3.8)), ((8, 12.2), (8, 14.2)), ((1.8, 8), (3.8, 8)), ((12.2, 8), (14.2, 8)),
                     ((3.6, 3.6), (5, 5)), ((11, 11), (12.4, 12.4)), ((3.6, 12.4), (5, 11)), ((11, 5), (12.4, 3.6))):
            p.drawLine(QPointF(*a), QPointF(*b))
    elif kind == "power":
        p.drawLine(QPointF(8, 2), QPointF(8, 8))
        p.drawArc(QRectF(3, 3.4, 10, 10), 125 * 16, 290 * 16)
    elif kind == "screen":
        p.drawRoundedRect(QRectF(2, 3.2, 12, 8.8), 1.2, 1.2)
        p.drawLine(QPointF(5.6, 14), QPointF(10.4, 14))
    elif kind == "shield":
        path = QPainterPath(QPointF(8, 2))
        path.lineTo(12.8, 4)
        path.lineTo(12.8, 7.6)
        path.cubicTo(12.8, 10.8, 10.7, 13, 8, 14)
        path.cubicTo(5.3, 13, 3.2, 10.8, 3.2, 7.6)
        path.lineTo(3.2, 4)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "mic":
        p.drawRoundedRect(QRectF(5.6, 2, 4.8, 8), 2.4, 2.4)
        p.drawArc(QRectF(3.6, 4, 8.8, 8.8), 180 * 16, 180 * 16)
        p.drawLine(QPointF(8, 12.4), QPointF(8, 14.4))
    elif kind == "note":  # a sheet with three lines ("Minhas notas")
        p.drawRoundedRect(QRectF(3, 2, 10, 12), 1.5, 1.5)
        for y in (5.5, 8, 10.5):
            p.drawLine(QPointF(5.5, y), QPointF(10.5 if y != 10.5 else 8.5, y))
    p.end()
    return QIcon(pm)


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
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        # The default timer rounds 33 ms up to ~47 on Windows: 21 fps, measured 2026-11-06.
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self.update)

    def set_state(self, state: str) -> None:
        self.state = state
        self.update()

    def feed_voice(self, level: float) -> None:
        if self.isVisible():
            self._speaking.feed_level(level, time.monotonic() - self._t0)

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
        dot.setPixmap(QPixmap.fromImage(sphere_image(28)).scaled(
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


class MainWindow(QWidget):
    settings_requested = Signal()
    hidden_to_tray = Signal()
    notes_requested = Signal()  # "Minhas notas" opened or refreshed: fetch the list
    note_delete_requested = Signal(str)  # the player confirmed a delete
    game_pick_requested = Signal()  # "Trocar" on the game card: open the game picker

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        ensure_fonts_loaded()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Ombri")
        self.setWindowIcon(QIcon(QPixmap.fromImage(sphere_image(256))))
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
        self.showing_notes = False
        self.notes: list[dict] | None = None  # None = loading
        self.notes_error = ""

        root = QWidget(self)
        root.setObjectName("root")
        root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        root.setGeometry(0, 0, WINDOW_W, WINDOW_H)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.titlebar = TitleBar(self)
        self.titlebar.hide_requested.connect(self._hide_to_tray)
        outer.addWidget(self.titlebar)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_waiting())
        self.stack.addWidget(self._build_status())
        self.stack.addWidget(self._build_notes())
        self._refresh()

    # ------------------------------------------------------------------ views
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
        self.w_orb = OrbWidget(216, 56, glow=0.8, halo=76)
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
        self.w_settings = QPushButton()
        self.w_settings.setObjectName("btn")
        self.w_settings.setFixedHeight(44)
        self.w_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.w_settings.clicked.connect(self.settings_requested.emit)
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
        self.s_orb = OrbWidget(256, 66, ring=88, glow=0.8)
        hero.addWidget(self.s_orb, 0, Qt.AlignmentFlag.AlignHCenter)
        self.s_state = _label(size=22, weight=600, heading=True)
        self.s_state.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        hero.addSpacing(-34)
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
        self.s_bar = QProgressBar()
        self.s_bar.setFixedHeight(6)
        self.s_bar.setTextVisible(False)
        self.s_bar.setRange(0, 100)
        u.addWidget(self.s_bar)
        col.addWidget(usage)

        self.s_keys = QGridLayout()
        self.s_keys.setSpacing(8)
        col.addLayout(self.s_keys)

        self.s_notes = QPushButton()
        self.s_notes.setObjectName("btn")
        self.s_notes.setFixedHeight(40)
        self.s_notes.setCursor(Qt.CursorShape.PointingHandCursor)
        self.s_notes.setIcon(line_icon("note", TEXT))
        self.s_notes.clicked.connect(self.open_notes)
        self.s_notes.hide()  # until the server says it has the notes endpoints
        col.addWidget(self.s_notes)

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
        self.s_settings.clicked.connect(self.settings_requested.emit)
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
        self.w_settings.setText(ui(L, "settings"))
        self.s_footer.setText(ui(L, "footer"))

        self.w_title.setText(ui(L, "wait_title"))
        self.w_body.setText(ui(L, "wait_body", **kw))
        self.w_body.setFixedHeight(self.w_body.heightForWidth(330))
        self.w_tips_caps.setText(ui(L, "wait_tips"))
        for lab, key in zip(self.w_tips, ("tip_window", "tip_admin", "tip_mic")):
            lab.setText(f"<span style='font-weight:600'>{ui(L, key + '_b')}</span> "
                        f"<span style='color:{TEXT_2}'>{ui(L, key)}</span>")
        self.stack.setCurrentIndex(2 if self.showing_notes else (1 if self.game else 0))

    def set_state(self, state: str) -> None:
        self.state = state
        self._refresh()

    def set_connection(self, conn: str) -> None:
        self.conn = conn
        self._refresh()

    def set_game(self, name: str | None) -> None:
        if name:  # sticky, like the client's own active_game
            self.game = name
            self._refresh()

    def set_voice_level(self, level: float) -> None:
        self.s_orb.feed_voice(level)
        self.w_orb.feed_voice(level)

    def set_usage(self, count: int, cap: int) -> None:
        self.usage = (count, cap)
        self._refresh()

    def set_info(self, lang: str, voice_label: str, keys: dict) -> None:
        self.lang = lang if lang in UI else "pt"
        self.voice_label = voice_label
        self.keys = {"ask": keys.get("ask", "F8"), "note": keys.get("note", "F6"), "pad": keys.get("pad")}
        self._rebuild_keys()
        self._refresh()

    # ------------------------------------------------------------------ notes
    def set_features(self, features: list) -> None:
        self.notes_enabled = "notes" in {str(f) for f in features}
        if not self.notes_enabled:
            self.showing_notes = False
        self._refresh()

    def set_notes(self, items: list[dict], error: str = "") -> None:
        self.notes = [n for n in items if isinstance(n, dict)]
        self.notes_error = error
        self._render_notes()

    def open_notes(self) -> None:
        """The button: show the page in its loading state and ask main.py."""
        self.showing_notes = True
        self.notes, self.notes_error = None, ""
        self._render_notes()
        self._refresh()
        self.notes_requested.emit()

    def close_notes(self) -> None:
        self.showing_notes = False
        self._refresh()

    # ------------------------------------------------------------------ window
    def show_and_raise(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _hide_to_tray(self) -> None:
        self.hide()
        if self.showing_notes:
            self.showing_notes = False  # reopens on the status page
            self._refresh()
        self.hidden_to_tray.emit()

    def closeEvent(self, event) -> None:  # noqa: N802 - Alt+F4 also only hides
        event.ignore()
        self._hide_to_tray()
