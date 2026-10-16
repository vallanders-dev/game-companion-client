"""client/gui/token_dialog.py — Ombri's settings window: tester token,
language and voice.

Runs as its OWN process (`python -m client.gui.token_dialog`), started by
client.main when there is no token yet or the server rejected the saved one,
and by the "Ombri - Configurações" shortcut (`--reason settings`) - same
reason the overlay is a separate process: client.main never imports Qt.
The token is checked against the real server before it's saved, so a typo
or a revoked token is caught here, with a plain explanation, not later as a
cryptic connection error.

Language and voice (2026-09-27): the language list picks Portuguese or
English (the whole conversation - speech recognition, answers, spoken
lines, console text); the voice list shows that language's voices
(client/voices.py) and "Ouvir"/"Listen" plays the voice's baked sample line.
The window's own text follows the chosen language. On a first launch the
language starts from Windows' display language; otherwise from the saved
voice. In `settings` mode the token may stay empty (= keep the saved one).

Exit code 0: settings saved (config.USER_SETTINGS_PATH).
Exit code 1: the player closed the window without saving.
"""
from __future__ import annotations

import argparse
import re
import sys
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QSlider, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from client import capture, voices
from client.config import (
    USER_SETTINGS_PATH, load_client_settings, load_duck_settings, load_follow_up_seconds, load_saved_token, load_voice_commands, load_show_in_recordings,
    save_user_settings,
)
from client.gui.fonts import body_font, ensure_fonts_loaded, heading_font
from client.gui.theme import DARK_THEME as T
from client.net import AuthError, ServerError, ServerSession

REASONS = ("missing", "rejected", "settings")

UI = {
    "pt": {
        "title": "Ombri — acesso ao beta",
        "missing": "Cole abaixo o token de testador que você recebeu e escolha a voz do Ombri.",
        "rejected": "O servidor não aceitou o token salvo. Cole um token válido abaixo.",
        "settings": "Escolha o idioma e a voz do Ombri. Deixe o token em branco pra manter o atual.",
        "language": "Idioma",
        "voice": "Voz",
        "listen": "Ouvir",
        "token": "Token",
        "token_placeholder": "seu token de testador",
        "token_keep": "(token salvo - deixe em branco pra manter)",
        "cancel": "Cancelar",
        "connect": "Conectar",
        "save": "Salvar",
        "need_token": "Cole o token primeiro.",
        "checking": "Verificando com o servidor...",
        "connected": "Conectado como {name}. Pode jogar!",
        "saved": "Salvo! Vale a partir da próxima pergunta.",
        "rejected_status": "Esse token não foi aceito. Confira se copiou ele inteiro.",
        "unreachable": "Não consegui falar com o servidor. Verifique sua internet e tente de novo.",
        "remembered": "Jogos lembrados",
        "remembered_count": "{n} jogo(s) que você nomeou por voz",
        "remembered_none": "nenhum",
        "forget": "Esquecer",
        "forgotten": "Esquecidos. Da próxima vez que um desses jogos abrir, o Ombri pergunta o nome de novo.",
        "duck": "Volume do jogo",
        "duck_on": "Abaixar o jogo quando eu falo",
        "duck_level": "O jogo fica em {n}% enquanto eu falo",
        "rec": "Gravações",
        "rec_on": "Aparecer nas gravações e lives (OBS, Discord)",
        "follow": "Conversa",
        "follow_on": "Continuar ouvindo depois que eu respondo",
        "vcmd": "Comandos de voz",
        "vcmd_on": "Ouvir \"Ei, Ombri\" e \"chega\" (tudo no seu PC)",
    },
    "en": {
        "title": "Ombri — beta access",
        "missing": "Paste the tester token you received below and choose Ombri's voice.",
        "rejected": "The server didn't accept the saved token. Paste a valid token below.",
        "settings": "Choose Ombri's language and voice. Leave the token empty to keep the current one.",
        "language": "Language",
        "voice": "Voice",
        "listen": "Listen",
        "token": "Token",
        "token_placeholder": "your tester token",
        "token_keep": "(token saved - leave empty to keep it)",
        "cancel": "Cancel",
        "connect": "Connect",
        "save": "Save",
        "need_token": "Paste the token first.",
        "checking": "Checking with the server...",
        "connected": "Connected as {name}. Have fun!",
        "saved": "Saved! It applies from your next question.",
        "rejected_status": "That token wasn't accepted. Make sure you copied all of it.",
        "unreachable": "I couldn't reach the server. Check your internet and try again.",
        "remembered": "Remembered games",
        "remembered_count": "{n} game(s) you named by voice",
        "remembered_none": "none",
        "forget": "Forget",
        "forgotten": "Forgotten. Next time one of those games opens, Ombri asks for its name again.",
        "duck": "Game volume",
        "duck_on": "Lower the game while I talk",
        "duck_level": "The game plays at {n}% while I talk",
        "rec": "Recordings",
        "rec_on": "Show me in recordings and streams (OBS, Discord)",
        "follow": "Conversation",
        "follow_on": "Keep listening after I answer",
        "vcmd": "Voice commands",
        "vcmd_on": "Listen for \"Hey Ombri\" and \"stop\" (all on your PC)",
    },
}

STYLE = f"""
QDialog {{ background: #14161C; }}
QLabel {{ color: {T.text_secondary}; }}
QLabel#title {{ color: {T.text_primary}; }}
QLineEdit, QComboBox {{
    background: #1D2028; color: {T.text_primary}; border: 1px solid #2A2E38;
    border-radius: 8px; padding: 8px 10px; selection-background-color: {T.accent};
}}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {T.accent}; }}
QComboBox QAbstractItemView {{
    background: #1D2028; color: {T.text_primary}; selection-background-color: {T.accent};
    selection-color: #14161C; border: 1px solid #2A2E38;
}}
QPushButton {{
    border-radius: 8px; padding: 9px 18px; color: {T.text_primary}; background: #252934;
}}
QPushButton#primary {{ background: {T.accent}; color: #14161C; }}
QPushButton:disabled {{ background: #2A2E38; color: {T.text_muted}; }}
QCheckBox {{ color: {T.text_primary}; spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #2A2E38; background: #1D2028; }}
QCheckBox::indicator:checked {{ background: {T.accent}; border: 1px solid {T.accent}; }}
QSlider::groove:horizontal {{ height: 4px; background: #2A2E38; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {T.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {T.text_primary}; width: 14px; margin: -6px 0; border-radius: 7px; }}
QSlider::sub-page:horizontal:disabled {{ background: #3A3F4B; }}
QSlider::handle:horizontal:disabled {{ background: #4A4F5B; }}
QLabel:disabled {{ color: {T.text_muted}; }}
"""


def clean_token(raw: str) -> str:
    """Accepts the token alone or the whole `SERVER_AUTH_TOKEN=...` line from
    the invite message, and drops spaces/line breaks picked up by the paste."""
    text = raw.strip()
    text = re.sub(r"^\s*SERVER_AUTH_TOKEN\s*=\s*", "", text)
    return re.sub(r"\s+", "", text)


def windows_language() -> str:
    """"pt" when Windows' display language is Portuguese, else "en"."""
    try:
        import ctypes

        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return "pt" if (langid & 0x3FF) == 0x16 else "en"  # LANG_PORTUGUESE
    except Exception:  # noqa: BLE001
        return "pt"


class TokenDialog(QDialog):
    def __init__(self, server_url: str, reason: str) -> None:
        super().__init__()
        self.server_url = server_url
        self.reason = reason if reason in REASONS else "missing"
        self.saved_token = load_saved_token()
        self.setMinimumWidth(500)
        self.setStyleSheet(STYLE)
        self._result: tuple[str, str] | None = None
        self._worker: threading.Thread | None = None
        self._pending_token = ""

        if USER_SETTINGS_PATH.exists() or self.reason != "missing":
            start_voice = voices.get(load_client_settings().voice)
        else:
            start_voice = voices.get(voices.DEFAULT_VOICE_FOR[windows_language()])
        self.ui_language = start_voice.language

        self.title = QLabel("Ombri")
        self.title.setObjectName("title")
        self.title.setFont(heading_font(QFont.Weight.Bold, 20))
        self.intro = QLabel()
        self.intro.setWordWrap(True)
        self.intro.setFont(body_font(QFont.Weight.Normal, 10.5))

        self.language_label, self.voice_label, self.token_label = QLabel(), QLabel(), QLabel()
        for lbl in (self.language_label, self.voice_label, self.token_label):
            lbl.setFont(body_font(QFont.Weight.DemiBold, 10))
        self.language_box = QComboBox()
        for code, name in voices.LANGUAGES.items():
            self.language_box.addItem(name, code)
        self.language_box.setCurrentIndex(self.language_box.findData(start_voice.language))
        self.voice_box = QComboBox()
        self.listen_btn = QPushButton()
        self.listen_btn.clicked.connect(self._listen)
        self.field = QLineEdit()
        self.field.returnPressed.connect(self._submit)
        # Games the player named by voice (capture.py pass 4) - settings only.
        self.remembered_label, self.remembered_info = QLabel(), QLabel()
        self.remembered_label.setFont(body_font(QFont.Weight.DemiBold, 10))
        self.remembered_info.setFont(body_font(QFont.Weight.Normal, 10))
        self.forget_btn = QPushButton()
        self.forget_btn.clicked.connect(self._forget_games)
        # Lower the game while Ombri speaks (client/ducking.py) - settings only.
        duck_on, duck_level = load_duck_settings()
        self.duck_label = QLabel()
        self.duck_label.setFont(body_font(QFont.Weight.DemiBold, 10))
        self.duck_box = QCheckBox()
        self.duck_box.setChecked(duck_on)
        self.duck_box.setFont(body_font(QFont.Weight.Medium, 10.5))
        self.duck_slider = QSlider(Qt.Orientation.Horizontal)
        self.duck_slider.setRange(1, 8)  # 10% .. 80%, in steps of 10
        self.duck_slider.setValue(max(1, min(8, round(duck_level * 10))))
        self.duck_value = QLabel()
        self.duck_value.setFont(body_font(QFont.Weight.Normal, 10))
        self.duck_box.toggled.connect(self._duck_changed)
        self.rec_label = QLabel()
        self.rec_label.setFont(body_font(QFont.Weight.DemiBold, 10))
        self.rec_box = QCheckBox()
        self.rec_box.setChecked(load_show_in_recordings())
        self.rec_box.setFont(body_font(QFont.Weight.Medium, 10.5))
        # The follow-up window (client/main.py) - settings only.
        self.follow_label = QLabel()
        self.follow_label.setFont(body_font(QFont.Weight.DemiBold, 10))
        self.follow_box = QCheckBox()
        self.follow_box.setChecked(load_follow_up_seconds() > 0)
        self.follow_box.setFont(body_font(QFont.Weight.Medium, 10.5))
        # "Ei, Ombri" / "chega" (client/voice_commands.py) - opt-in, settings only.
        self.vcmd_label = QLabel()
        self.vcmd_label.setFont(body_font(QFont.Weight.DemiBold, 10))
        self.voice_cmd_box = QCheckBox()
        self.voice_cmd_box.setChecked(load_voice_commands())
        self.voice_cmd_box.setFont(body_font(QFont.Weight.Medium, 10.5))
        self.duck_slider.valueChanged.connect(self._duck_changed)
        for w in (self.language_box, self.voice_box, self.field):
            w.setFont(body_font(QFont.Weight.Medium, 10.5))

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setFont(body_font(QFont.Weight.Normal, 10))

        self.cancel_btn = QPushButton()
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn = QPushButton()
        self.ok_btn.setObjectName("primary")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._submit)
        for b in (self.cancel_btn, self.ok_btn, self.listen_btn, self.forget_btn):
            b.setFont(body_font(QFont.Weight.DemiBold, 10.5))
            b.setCursor(Qt.CursorShape.PointingHandCursor)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.addWidget(self.language_label, 0, 0)
        grid.addWidget(self.language_box, 0, 1, 1, 2)
        grid.addWidget(self.voice_label, 1, 0)
        grid.addWidget(self.voice_box, 1, 1)
        grid.addWidget(self.listen_btn, 1, 2)
        grid.addWidget(self.token_label, 2, 0)
        grid.addWidget(self.field, 2, 1, 1, 2)
        if self.reason == "settings":
            grid.addWidget(self.remembered_label, 3, 0)
            grid.addWidget(self.remembered_info, 3, 1)
            grid.addWidget(self.forget_btn, 3, 2)
            grid.addWidget(self.duck_label, 4, 0)
            grid.addWidget(self.duck_box, 4, 1, 1, 2)
            grid.addWidget(self.duck_slider, 5, 1, 1, 2)
            grid.addWidget(self.duck_value, 6, 1, 1, 2)
            grid.addWidget(self.rec_label, 7, 0)
            grid.addWidget(self.rec_box, 7, 1, 1, 2)
            grid.addWidget(self.follow_label, 8, 0)
            grid.addWidget(self.follow_box, 8, 1, 1, 2)
            grid.addWidget(self.vcmd_label, 9, 0)
            grid.addWidget(self.voice_cmd_box, 9, 1, 1, 2)
        grid.setColumnStretch(1, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.ok_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(self.title)
        layout.addWidget(self.intro)
        layout.addLayout(grid)
        layout.addWidget(self.status)
        layout.addLayout(buttons)

        self._fill_voices(start_voice.key)
        self._relabel()
        self.language_box.currentIndexChanged.connect(self._language_changed)

        self._poll = QTimer(self)
        self._poll.setInterval(100)
        self._poll.timeout.connect(self._check_result)

    # -- language / voice ----------------------------------------------------
    def _s(self, key: str, **kw) -> str:
        text = UI[self.ui_language][key]
        return text.format(**kw) if kw else text

    def _fill_voices(self, select: str | None = None) -> None:
        self.voice_box.clear()
        for v in voices.for_language(self.ui_language):
            self.voice_box.addItem(voices.label(v, self.ui_language), v.key)
        idx = self.voice_box.findData(select or voices.DEFAULT_VOICE_FOR[self.ui_language])
        self.voice_box.setCurrentIndex(max(0, idx))

    def _relabel(self) -> None:
        self.setWindowTitle(self._s("title"))
        self.intro.setText(self._s(self.reason))
        self.language_label.setText(self._s("language"))
        self.voice_label.setText(self._s("voice"))
        self.token_label.setText(self._s("token"))
        self.listen_btn.setText(self._s("listen"))
        self.cancel_btn.setText(self._s("cancel"))
        self.ok_btn.setText(self._s("save" if self.reason == "settings" else "connect"))
        keep = self.reason == "settings" and self.saved_token
        self.field.setPlaceholderText(self._s("token_keep" if keep else "token_placeholder"))
        self.remembered_label.setText(self._s("remembered"))
        self.forget_btn.setText(self._s("forget"))
        n = capture.remembered_count()
        self.remembered_info.setText(self._s("remembered_count", n=n) if n else self._s("remembered_none"))
        self.forget_btn.setEnabled(n > 0)
        self.duck_label.setText(self._s("duck"))
        self.duck_box.setText(self._s("duck_on"))
        self.rec_label.setText(self._s("rec"))
        self.rec_box.setText(self._s("rec_on"))
        self.follow_label.setText(self._s("follow"))
        self.follow_box.setText(self._s("follow_on"))
        self.vcmd_label.setText(self._s("vcmd"))
        self.voice_cmd_box.setText(self._s("vcmd_on"))
        self._duck_changed()

    def _duck_changed(self, *_args) -> None:
        on = self.duck_box.isChecked()
        self.duck_slider.setEnabled(on)
        self.duck_value.setEnabled(on)
        self.duck_value.setText(self._s("duck_level", n=self.duck_slider.value() * 10))

    def _duck_fields(self) -> dict:
        if self.reason != "settings":
            return {}
        return {"duck_game": self.duck_box.isChecked(), "duck_level": self.duck_slider.value() / 10,
                "show_in_recordings": self.rec_box.isChecked(), "follow_up": self.follow_box.isChecked(),
                "voice_commands": self.voice_cmd_box.isChecked()}

    def _forget_games(self) -> None:
        capture.forget_remembered_games()
        self._relabel()
        self._set_status(self._s("forgotten"), T.states.listening)

    def _language_changed(self) -> None:
        self.ui_language = self.language_box.currentData()
        self._fill_voices()
        self._relabel()
        self.status.setText("")

    def selected_voice(self) -> str:
        return self.voice_box.currentData() or voices.DEFAULT_VOICE

    def _listen(self) -> None:
        voice = self.selected_voice()

        def play() -> None:
            try:
                from client.fixed_audio import play_line

                play_line("sample", voice, fallback_tone=False)
            except Exception:  # noqa: BLE001 - no audio device: nothing to hear, nothing to break
                pass

        threading.Thread(target=play, daemon=True).start()

    # -- saving ----------------------------------------------------------------
    def _set_status(self, text: str, color: str) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color};")

    def _submit(self) -> None:
        if self._worker is not None:
            return
        token = clean_token(self.field.text())
        if not token and self.reason == "settings" and self.saved_token:
            # Only language/voice changed: nothing to check with the server.
            save_user_settings(voice=self.selected_voice(), **self._duck_fields())
            self._set_status(self._s("saved"), T.states.listening)
            QTimer.singleShot(900, self.accept)
            return
        if not token:
            self._set_status(self._s("need_token"), T.states.thinking)
            return
        self.field.setText(token)
        self.ok_btn.setEnabled(False)
        self.field.setEnabled(False)
        self._set_status(self._s("checking"), T.text_secondary)

        def check() -> None:
            session = ServerSession(self.server_url, token)
            try:
                name = session.connect(timeout=15)
                self._result = ("ok", name)
            except AuthError:
                self._result = ("rejected", "")
            except ServerError:
                self._result = ("unreachable", "")
            finally:
                session.close()

        self._pending_token = token
        self._worker = threading.Thread(target=check, daemon=True)
        self._worker.start()
        self._poll.start()

    def _check_result(self) -> None:
        if self._result is None:
            return
        self._poll.stop()
        kind, name = self._result
        self._result = None
        self._worker = None
        if kind == "ok":
            save_user_settings(token=self._pending_token, voice=self.selected_voice(), **self._duck_fields())
            self._set_status(self._s("connected", name=name), T.states.listening)
            QTimer.singleShot(900, self.accept)
            return
        self.ok_btn.setEnabled(True)
        self.field.setEnabled(True)
        self.field.setFocus()
        if kind == "rejected":
            self._set_status(self._s("rejected_status"), T.accent)
        else:
            self._set_status(self._s("unreachable"), T.states.thinking)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reason", choices=REASONS, default="missing")
    args = ap.parse_args(argv)
    from client.gui.orb import claim_taskbar_identity, sphere_image
    from PySide6.QtGui import QIcon, QPixmap

    claim_taskbar_identity()
    app = QApplication.instance() or QApplication(sys.argv[:1])  # noqa: F841 - must exist before widgets
    app.setWindowIcon(QIcon(QPixmap.fromImage(sphere_image(256))))
    ensure_fonts_loaded()
    dialog = TokenDialog(load_client_settings().server_url, args.reason)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return 0 if dialog.exec() == QDialog.DialogCode.Accepted else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
