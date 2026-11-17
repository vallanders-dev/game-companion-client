"""client/gui/ui_process.py — python -m client.gui.ui_process

Every piece of Ombri's desktop UI, as ONE standalone Qt process driven by
client/gui/client.py (see that module for why the UI is never a Qt loop
inside the voice client). Until 2026-09-29 this was overlay_process.py and
held only the in-game orb; stage 1 of the desktop UI added, to the approved
mockup:

  * the main window (client/gui/main_window.py) - shown at launch, hidden
    to the tray by its minimize/close buttons;
  * the tray icon and its menu: Abrir Ombri, Pausar/Retomar, Configurações,
    Sair (Qt's own tray icon - the pystray one in client/tray.py is no
    longer used by the client);
  * notices as a small pop-up above the tray (the game-runs-as-admin one
    has a "Reabrir como admin" button), shown without taking focus from
    the game.

The in-game orb (OverlayWindow) still starts hidden and appears the first
time a "game" message carries a real name, then stays.

stdin, JSON lines from the client:
  {"type": "state", "state": "idle|ouvindo|pensando|pesquisando|falando|pausado"}
  {"type": "voice", "level": 0.0-1.0}   (the voice's loudness, ~10 times a second while it plays)
  {"type": "append", "text": str}              (ignored - no text display)
  {"type": "game", "name": str|null}
  {"type": "usage", "count": int, "cap": int}
  {"type": "conn", "state": "connecting|ok|offline"}
  {"type": "info", "lang": "pt|en|es", "voice": str, "keys": {"ask", "note", "pad"}}
  {"type": "paused", "on": bool}
  {"type": "notice", "kind": "admin|offline|update|announce|survey", "version"?, "text"?}
  {"type": "show"}
  {"type": "features", "list": [str]}            # what the server can do ("notes", "recap")
  {"type": "notes", "items": [{id, game, note, created, source}], "error": str}
stdout, JSON lines back to the client (the ONLY thing written there - every
diagnostic print goes to stderr, i.e. output/overlay.log):
  {"cmd": "quit" | "pause" | "resume" | "settings" | "relaunch_admin" | "survey_open" | "survey_later"
          | "notes_list" | "notes_delete:<id>"}
Exits on stdin EOF, i.e. whenever the client exits.
"""
from __future__ import annotations

import ctypes
import json
import signal
import sys
import threading
import time

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QSystemTrayIcon, QVBoxLayout,
    QWidget, QWidgetAction,
)

from client.gui.fonts import BODY_FAMILY, HEADING_FAMILY, ensure_fonts_loaded
from client.gui.main_window import CHIP_TEXT, TEXT, TEXT_2, MainWindow, line_icon
from client.gui.orb import claim_taskbar_identity, sphere_image
from client.gui.overlay import DARK_THEME, HudState, OverlayWindow
from client.gui.ui_texts import ui

STATES = {s.value: s for s in HudState}
NOTICE_SECONDS = 20
RECAP_NOTICE_SECONDS = 90  # the game just closed: give the player time to see it (server keeps it 30 min)

MENU_QSS = f"""
QMenu {{ background: #2B2B2B; border: 1px solid #3A3A3A; border-radius: 8px; padding: 4px; }}
QMenu::item {{ color: {TEXT}; font-family: "{BODY_FAMILY}"; font-size: 14px; padding: 9px 14px 9px 10px;
               border-radius: 4px; }}
QMenu::item:selected {{ background: #383838; }}
QMenu::icon {{ padding-left: 10px; }}
QMenu::separator {{ height: 1px; background: #3D3D3D; margin: 4px 0; }}
"""

TOAST_QSS = f"""
QFrame#toast {{ background: #202020; border: 1px solid #333333; border-radius: 8px; }}
QLabel {{ color: {TEXT}; font-family: "{BODY_FAMILY}"; background: transparent; }}
QPushButton {{ height: 36px; border: none; border-radius: 6px; font-family: "{BODY_FAMILY}"; font-size: 13px; }}
QPushButton#yes {{ background: #FF6B4A; color: #1A0E0A; font-weight: 700; }}
QPushButton#yes:hover {{ background: #FF8062; }}
QPushButton#no {{ background: #3A3A3A; color: {TEXT}; font-weight: 600; }}
QPushButton#no:hover {{ background: #454545; }}
"""


def _send(cmd: str) -> None:
    out = _COMMAND_OUT
    try:
        out.write(json.dumps({"cmd": cmd}) + "\n")
        out.flush()
    except (OSError, ValueError):
        pass


_COMMAND_OUT = sys.stdout


class _Bridge(QObject):
    message = Signal(object)
    eof = Signal()


def _read_stdin(bridge: _Bridge) -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            bridge.message.emit(json.loads(line))
        except ValueError:
            print(f"[ui-process] linha inválida ignorada: {line!r}", flush=True)
    bridge.eof.emit()


def _no_activate(widget: QWidget) -> None:
    """WS_EX_NOACTIVATE: the pop-up can be clicked but never pulls focus
    (and so never pauses) the game - same fix as the overlay's."""
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.WinDLL("user32")
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        hwnd = ctypes.c_void_p(int(widget.winId()))
        style = user32.GetWindowLongPtrW(hwnd, -20)
        user32.SetWindowLongPtrW(hwnd, -20, style | 0x08000000)
    except (OSError, AttributeError, ValueError):
        pass


class Notice(QWidget):
    """The mockup's notification: above the tray, bottom-right."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(TOAST_QSS)
        self.setFixedWidth(364)
        frame = QFrame(self)
        frame.setObjectName("toast")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(frame)
        col = QVBoxLayout(frame)
        col.setContentsMargins(16, 16, 16, 16)
        col.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(8)
        dot = QLabel()
        dot.setPixmap(QPixmap.fromImage(sphere_image(28)).scaled(
            14, 14, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        app = QLabel("Ombri")
        app.setStyleSheet(f"font-size: 12px; color: {CHIP_TEXT};")
        head.addWidget(dot)
        head.addWidget(app)
        head.addStretch(1)
        col.addLayout(head)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setStyleSheet(f"font-size: 13px; color: {CHIP_TEXT};")
        col.addWidget(self.title)
        col.addWidget(self.body)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.yes = QPushButton()
        self.yes.setObjectName("yes")
        self.no = QPushButton()
        self.no.setObjectName("no")
        for b in (self.yes, self.no):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            buttons.addWidget(b, 1)
        col.addSpacing(4)
        col.addLayout(buttons)
        self.no.clicked.connect(self._no)
        self.yes.clicked.connect(self._yes)
        self._on_yes = None
        self._on_no = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def _yes(self) -> None:
        self.hide()
        if self._on_yes:
            self._on_yes()

    def _no(self) -> None:
        self.hide()
        if self._on_no:
            self._on_no()

    def pop(self, title: str, body: str, yes: str | None, no: str | None, on_yes=None, on_no=None,
            seconds: int = NOTICE_SECONDS) -> None:
        self.title.setText(title)
        self.body.setText(body)
        self.yes.setVisible(bool(yes))
        self.no.setVisible(bool(no))
        self.yes.setText(yes or "")
        self.no.setText(no or "")
        self._on_yes = on_yes
        self._on_no = on_no
        self.adjustSize()
        geo = QApplication.primaryScreen().availableGeometry()
        self.move(QPoint(geo.right() - self.width() - 16, geo.bottom() - self.height() - 16))
        self.show()
        _no_activate(self)
        self._timer.start(seconds * 1000)


PICKER_QSS = TOAST_QSS + f"""
QComboBox {{ background: #2B2B2B; color: {TEXT}; border: 1px solid #3A3A3A; border-radius: 6px;
             padding: 6px 10px; font-family: "{BODY_FAMILY}"; font-size: 14px; min-height: 24px; }}
QComboBox QAbstractItemView {{ background: #2B2B2B; color: {TEXT}; selection-background-color: #383838; }}
"""
GAME_NAME_MAX = 80  # same cap as client/main.py


class GamePicker(QWidget):
    """Pick the game from the list or type it (owner, 2026-11-12): the same
    choice as answering "qual jogo?" out loud. Unlike the notice it takes
    focus, because typing needs it - so it only opens on the player's click."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(PICKER_QSS)
        self.setFixedWidth(380)
        frame = QFrame(self)
        frame.setObjectName("toast")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(frame)
        col = QVBoxLayout(frame)
        col.setContentsMargins(16, 16, 16, 16)
        col.setSpacing(10)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.body = QLabel()
        self.body.setWordWrap(True)
        self.body.setStyleSheet(f"font-size: 13px; color: {CHIP_TEXT};")
        self.box = QComboBox()
        self.box.setEditable(True)
        self.box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.box.lineEdit().setMaxLength(GAME_NAME_MAX)
        self.box.lineEdit().returnPressed.connect(self._use)
        self.box.lineEdit().textChanged.connect(self._enable)
        completer = self.box.completer()
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        for w in (self.title, self.body, self.box):
            col.addWidget(w)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.yes = QPushButton()
        self.yes.setObjectName("yes")
        self.no = QPushButton()
        self.no.setObjectName("no")
        for b in (self.yes, self.no):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            buttons.addWidget(b, 1)
        col.addSpacing(4)
        col.addLayout(buttons)
        self.yes.clicked.connect(self._use)
        self.no.clicked.connect(self.hide)
        self.games: list[str] = []

    def set_games(self, names: list[str]) -> None:
        typed = self.box.currentText()
        self.games = sorted({str(n).strip() for n in names if str(n).strip()}, key=str.casefold)
        self.box.clear()
        self.box.addItems(self.games)
        self.box.setEditText(typed)

    def open(self, lang: str, current: str | None) -> None:
        self.title.setText(ui(lang, "pick_title"))
        self.body.setText(ui(lang, "pick_body"))
        self.box.lineEdit().setPlaceholderText(ui(lang, "pick_placeholder"))
        self.yes.setText(ui(lang, "pick_yes"))
        self.no.setText(ui(lang, "pick_no"))
        self.box.setEditText("")
        self._enable()
        _send("games_list")  # fresh list; typing works while it arrives
        self.adjustSize()
        geo = QApplication.primaryScreen().availableGeometry()
        self.move(QPoint(geo.right() - self.width() - 16, geo.bottom() - self.height() - 16))
        self.show()
        self.raise_()
        self.activateWindow()
        self.box.setFocus()

    def _enable(self) -> None:
        self.yes.setEnabled(bool(self.box.currentText().strip()))

    def chosen(self) -> str:
        """The list's own spelling when the typed name matches one ignoring
        case and spaces (one game key per game); otherwise as typed."""
        typed = " ".join(self.box.currentText().split())[:GAME_NAME_MAX]
        key = typed.casefold()
        return next((g for g in self.games if " ".join(g.split()).casefold() == key), typed)

    def _use(self) -> None:
        name = self.chosen()
        if not name:
            return
        self.hide()
        _send("game_set:" + name)


class TrayHeader(QWidget):
    """The menu's first row: orb, name, connection · game."""

    def __init__(self) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 8)
        row.setSpacing(10)
        orb = QLabel()
        orb.setPixmap(QPixmap.fromImage(sphere_image(56)).scaled(
            28, 28, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        row.addWidget(orb)
        col = QVBoxLayout()
        col.setSpacing(2)
        name = QLabel("Ombri")
        name.setStyleSheet(f'font-family: "{HEADING_FAMILY}"; font-size: 15px; font-weight: 600; color: {TEXT};')
        self.status = QLabel()
        self.status.setStyleSheet(f'font-family: "{BODY_FAMILY}"; font-size: 12px; color: {TEXT_2};')
        col.addWidget(name)
        col.addWidget(self.status)
        row.addLayout(col, 1)
        self.setMinimumWidth(260)


class Ui:
    def __init__(self, app: QApplication, start_in_tray: bool = False) -> None:
        self.app = app
        self.start_in_tray = start_in_tray
        self.window = MainWindow()
        self.overlay = OverlayWindow(theme=DARK_THEME)
        self.notice = Notice()
        self.picker = GamePicker()
        self.revealed = False
        self.paused = False
        self.told_tray = False
        self.state = "idle"

        self.window.settings_requested.connect(lambda: _send("settings"))
        self.window.hidden_to_tray.connect(self._hidden)
        # "Minhas notas" (docs/player_notes.md): the window asks, main.py
        # answers with a "notes" message after calling the server.
        self.window.notes_requested.connect(lambda: _send("notes_list"))
        self.window.note_delete_requested.connect(lambda note_id: _send("notes_delete:" + note_id))
        self.window.game_pick_requested.connect(self.open_picker)

        self.tray = QSystemTrayIcon(QIcon(QPixmap.fromImage(sphere_image(64))))
        self.tray.setToolTip("Ombri")
        self.menu = QMenu()
        self.menu.setWindowFlags(self.menu.windowFlags() | Qt.WindowType.FramelessWindowHint
                                 | Qt.WindowType.NoDropShadowWindowHint)
        self.menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.menu.setStyleSheet(MENU_QSS)
        self.header = TrayHeader()
        head_action = QWidgetAction(self.menu)
        head_action.setDefaultWidget(self.header)
        head_action.setEnabled(False)
        self.menu.addAction(head_action)
        self.menu.addSeparator()
        self.a_open = QAction(line_icon("window"), "", self.menu)
        self.a_game = QAction(line_icon("screen"), "", self.menu)
        self.a_pause = QAction(line_icon("pause"), "", self.menu)
        self.a_settings = QAction(line_icon("gear"), "", self.menu)
        self.a_quit = QAction(line_icon("power"), "", self.menu)
        self.a_open.triggered.connect(self.window.show_and_raise)
        self.a_game.triggered.connect(self.open_picker)
        self.a_pause.triggered.connect(lambda: _send("resume" if self.paused else "pause"))
        self.a_settings.triggered.connect(lambda: _send("settings"))
        self.a_quit.triggered.connect(lambda: _send("quit"))
        for a in (self.a_open, self.a_game, self.a_pause, self.a_settings):
            self.menu.addAction(a)
        self.menu.addSeparator()
        self.menu.addAction(self.a_quit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._tray_click)
        self.tray.show()
        self._texts()
        if not self.start_in_tray:
            # Not after "Reabrir como admin" (--tray): the game is in front then,
            # and raising this window took its focus - live 2026-10-16, the left
            # mouse button went dead in Witcher 3 (hotkeys don't need focus).
            self.window.show_and_raise()

    # ------------------------------------------------------------------ helpers
    def _texts(self) -> None:
        L = self.window.lang
        self.a_open.setText(ui(L, "tray_open"))
        self.a_game.setText(ui(L, "tray_game"))
        self.a_pause.setText(ui(L, "tray_resume" if self.paused else "tray_pause"))
        self.a_pause.setIcon(line_icon("play" if self.paused else "pause"))
        self.a_settings.setText(ui(L, "tray_settings"))
        self.a_quit.setText(ui(L, "tray_quit"))
        status = ui(L, f"conn_{self.window.conn}")
        if self.window.game:
            status += f" · {self.window.game}"
        self.header.status.setText(status)
        self.tray.setToolTip(f"Ombri · {status}")

    def open_picker(self) -> None:
        self.notice.hide()
        self.picker.open(self.window.lang, self.window.game)

    def _tray_click(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.window.show_and_raise()

    def _hidden(self) -> None:
        if not self.told_tray:
            self.told_tray = True
            self.tray.showMessage("Ombri", ui(self.window.lang, "still_running"),
                                  QIcon(QPixmap.fromImage(sphere_image(64))), 4000)

    def _show_state(self) -> None:
        shown = "pausado" if self.paused else self.state
        self.window.set_state(shown)
        hud = STATES.get(shown)
        if hud is not None:
            self.overlay.set_state(hud)

    # ------------------------------------------------------------------ messages
    def handle(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "state":
            if msg.get("state") not in STATES:
                print(f"[ui-process] estado desconhecido: {msg!r}", flush=True)
                return
            self.state = msg["state"]
            self._show_state()
            # Diagnostic kept from overlay_process.py (2026-09-25 orb-lag
            # report): client -> UI pipe latency, in output/overlay.log.
            t_sent = msg.get("t_sent")
            if t_sent is not None:
                print(f"[ui-process] state={self.state} pipe_latency={(time.time() - t_sent) * 1000:.1f}ms",
                      flush=True)
        elif kind == "voice":
            self.window.set_voice_level(max(0.0, min(1.0, float(msg.get("level", 0.0)))))
        elif kind == "game":
            name = msg.get("name")
            self.window.set_game(name)
            if name and not self.revealed:
                self.revealed = True
                self.overlay.show()
            self._texts()
        elif kind == "usage":
            count, cap = int(msg.get("count", 0)), int(msg.get("cap", 0))
            self.overlay.set_usage(count, cap)
            self.window.set_usage(count, cap)
        elif kind == "conn":
            self.window.set_connection(str(msg.get("state", "ok")))
            self._texts()
        elif kind == "info":
            self.window.set_info(str(msg.get("lang", "pt")), str(msg.get("voice", "")), msg.get("keys") or {})
            self._texts()
        elif kind == "paused":
            self.paused = bool(msg.get("on"))
            self._show_state()
            self._texts()
        elif kind == "notice":
            self._notice(str(msg.get("kind", "")), msg)
        elif kind == "show":
            self.window.show_and_raise()
        elif kind == "features":
            self.window.set_features(msg.get("list") or [])
        elif kind == "notes":
            self.window.set_notes(msg.get("items") or [], str(msg.get("error") or ""))
        elif kind == "games":
            self.picker.set_games([g for g in (msg.get("list") or []) if isinstance(g, str)])
        elif kind == "echo_cmd":
            # Test hook (eval/client_ui_harness.py): proves the command
            # channel end to end without a mouse. The client never sends it.
            _send(str(msg.get("cmd", "")))

    def _notice(self, kind: str, msg: dict | None = None) -> None:
        print(f"[ui-process] aviso mostrado: {kind}", flush=True)
        L, keys = self.window.lang, self.window.keys
        if kind == "admin":
            self.notice.pop(ui(L, "admin_title"), ui(L, "admin_body", ask=keys["ask"], note=keys["note"]),
                            ui(L, "admin_yes"), ui(L, "admin_no"), on_yes=lambda: _send("relaunch_admin"))
        elif kind == "offline":
            self.notice.pop(ui(L, "offline_title"), ui(L, "offline_body"), None, None)
        elif kind == "update":
            self.notice.pop(ui(L, "update_title"), ui(L, "update_body", version=str((msg or {}).get("version", ""))),
                            ui(L, "update_yes"), ui(L, "update_no"), on_yes=lambda: _send("restart_update"))
        elif kind == "announce" and (msg or {}).get("text"):
            self.notice.pop(ui(L, "announce_title"), str(msg["text"]), ui(L, "announce_ok"), None)
        elif kind == "survey":
            self.notice.pop(ui(L, "survey_title"), ui(L, "survey_body"), ui(L, "survey_yes"), ui(L, "survey_no"),
                            on_yes=lambda: _send("survey_open"), on_no=lambda: _send("survey_later"))
        elif kind == "recap" and (msg or {}).get("text") and (msg or {}).get("offer_id"):
            # The end-of-session card (owner, 2026-11-12): only "Sim, salva"
            # saves; "Não salva" or letting it go away saves nothing.
            offer_id = str(msg["offer_id"])
            self.notice.pop(ui(L, "recap_title", game=str(msg.get("game") or "")), str(msg["text"]),
                            ui(L, "recap_yes"), ui(L, "recap_no"),
                            on_yes=lambda: _send("recap_save:" + offer_id), seconds=RECAP_NOTICE_SECONDS)
        elif kind == "game_ask":
            # Ombri is asking "qual jogo?" out loud; the card offers the list too.
            self.notice.pop(ui(L, "pick_title"), ui(L, "game_ask_body"), ui(L, "game_ask_list"), None,
                            on_yes=self.open_picker)
        elif kind == "recap_saved":
            self.notice.pop(ui(L, "recap_saved_title"), ui(L, "recap_saved_body"), None, None, seconds=6)
        elif kind == "recap_failed":
            self.notice.pop(ui(L, "recap_failed_title"), ui(L, "recap_failed_body"), None, None, seconds=8)


def main() -> None:
    global _COMMAND_OUT
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # lifetime is tied to stdin, not Ctrl+C
    sys.stdin.reconfigure(encoding="utf-8")
    # stdout is the command channel back to the client; every print (ours,
    # the overlay's) goes to stderr = output/overlay.log instead.
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    _COMMAND_OUT = sys.stdout
    sys.stdout = sys.stderr

    claim_taskbar_identity()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # hiding the window to the tray must not end the UI
    ensure_fonts_loaded()
    ui_state = Ui(app, start_in_tray="--tray" in sys.argv[1:])
    bridge = _Bridge()
    bridge.message.connect(ui_state.handle)
    # exit(), not quit(): Qt 6's quit() first asks every window to close,
    # and the main window refuses (closing only hides it to the tray) - so a
    # quit() on stdin EOF was cancelled and the process outlived the client.
    bridge.eof.connect(lambda: app.exit(0))
    threading.Thread(target=_read_stdin, args=(bridge,), name="ui-stdin", daemon=True).start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
