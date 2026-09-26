"""client/gui/token_dialog.py — the first-launch "paste your tester token" window.

Runs as its OWN process (`python -m client.gui.token_dialog`), started by
client.main when there is no token yet or the server rejected the saved one -
same reason the overlay is a separate process: client.main never imports Qt.
The token is checked against the real server before it's saved, so a typo or
a revoked token is caught here, with a plain explanation, not later as a
cryptic connection error.

Exit code 0: a working token was saved (config.USER_SETTINGS_PATH).
Exit code 1: the player closed the window without one.
"""
from __future__ import annotations

import argparse
import re
import sys
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from client.config import load_client_settings, save_token
from client.gui.fonts import body_font, ensure_fonts_loaded, heading_font
from client.gui.theme import DARK_THEME as T
from client.net import AuthError, ServerError, ServerSession

REASONS = {
    "missing": "Cole abaixo o token de testador que você recebeu.",
    "rejected": "O servidor não aceitou o token salvo. Cole um token válido abaixo.",
}

STYLE = f"""
QDialog {{ background: #14161C; }}
QLabel {{ color: {T.text_secondary}; }}
QLabel#title {{ color: {T.text_primary}; }}
QLineEdit {{
    background: #1D2028; color: {T.text_primary}; border: 1px solid #2A2E38;
    border-radius: 8px; padding: 9px 10px; selection-background-color: {T.accent};
}}
QLineEdit:focus {{ border: 1px solid {T.accent}; }}
QPushButton {{
    border-radius: 8px; padding: 9px 18px; color: {T.text_primary}; background: #252934;
}}
QPushButton#primary {{ background: {T.accent}; color: #14161C; }}
QPushButton:disabled {{ background: #2A2E38; color: {T.text_muted}; }}
"""


def clean_token(raw: str) -> str:
    """Accepts the token alone or the whole `SERVER_AUTH_TOKEN=...` line from
    the invite message, and drops spaces/line breaks picked up by the paste."""
    text = raw.strip()
    text = re.sub(r"^\s*SERVER_AUTH_TOKEN\s*=\s*", "", text)
    return re.sub(r"\s+", "", text)


class TokenDialog(QDialog):
    def __init__(self, server_url: str, reason: str) -> None:
        super().__init__()
        self.server_url = server_url
        self.setWindowTitle("Parça — acesso ao beta")
        self.setMinimumWidth(460)
        self.setStyleSheet(STYLE)
        self._result: tuple[str, str] | None = None
        self._worker: threading.Thread | None = None

        title = QLabel("Parça")
        title.setObjectName("title")
        title.setFont(heading_font(QFont.Weight.Bold, 20))
        intro = QLabel(REASONS.get(reason, REASONS["missing"]))
        intro.setWordWrap(True)
        intro.setFont(body_font(QFont.Weight.Normal, 10.5))

        self.field = QLineEdit()
        self.field.setPlaceholderText("seu token de testador")
        self.field.setFont(body_font(QFont.Weight.Medium, 10.5))
        self.field.returnPressed.connect(self._submit)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setFont(body_font(QFont.Weight.Normal, 10))

        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn = QPushButton("Conectar")
        self.ok_btn.setObjectName("primary")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._submit)
        for b in (self.cancel_btn, self.ok_btn):
            b.setFont(body_font(QFont.Weight.DemiBold, 10.5))
            b.setCursor(Qt.CursorShape.PointingHandCursor)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.ok_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addWidget(intro)
        layout.addWidget(self.field)
        layout.addWidget(self.status)
        layout.addLayout(buttons)

        self._poll = QTimer(self)
        self._poll.setInterval(100)
        self._poll.timeout.connect(self._check_result)

    def _set_status(self, text: str, color: str) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color};")

    def _submit(self) -> None:
        if self._worker is not None:
            return
        token = clean_token(self.field.text())
        if not token:
            self._set_status("Cole o token primeiro.", T.states.thinking)
            return
        self.field.setText(token)
        self.ok_btn.setEnabled(False)
        self.field.setEnabled(False)
        self._set_status("Verificando com o servidor...", T.text_secondary)

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
            save_token(self._pending_token)
            self._set_status(f"Conectado como {name}. Pode jogar!", T.states.listening)
            QTimer.singleShot(900, self.accept)
            return
        self.ok_btn.setEnabled(True)
        self.field.setEnabled(True)
        self.field.setFocus()
        if kind == "rejected":
            self._set_status("Esse token não foi aceito. Confira se copiou ele inteiro.", T.accent)
        else:
            self._set_status("Não consegui falar com o servidor. Verifique sua internet e tente de novo.",
                             T.states.thinking)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reason", choices=sorted(REASONS), default="missing")
    args = ap.parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])  # noqa: F841 - must exist before widgets
    ensure_fonts_loaded()
    dialog = TokenDialog(load_client_settings().server_url, args.reason)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return 0 if dialog.exec() == QDialog.DialogCode.Accepted else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
