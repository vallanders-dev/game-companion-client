"""client/gui/settings_panel.py — the Configurações view of the main window
(docs/agentic_chat.md, phase 1, 2026-11-21).

The same fields the settings window had (client/gui/token_dialog.py with
`--reason settings`, its own process until now): language, voice and
"Ouvir", the token (empty = keep the saved one), the games named by voice
and "Esquecer", the game's volume while Ombri talks and its slider,
recordings, the follow-up window, voice commands, the play history (saved
only when changed here). The words are the dialog's own table
(token_dialog.UI), so the two never say different things; the save is
client/gui/settings_save.submit(), the same function the dialog calls.

What happens after a save is what happened after the dialog closed: nothing
is sent to client/main.py. The running copy reads the file between turns
(the voice and language at its next poll, voice commands, ducking, the
follow-up window and recordings at the next answer); a new token applies at
the next start, as before. The panel reloads its fields from the file each
time the view opens (`reload()`), so a value changed elsewhere shows.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSlider, QVBoxLayout,
    QWidget,
)

from client import capture, voices
from client.config import (
    _read_user_settings, load_client_settings, load_duck_settings, load_follow_up_seconds, load_saved_token,
    load_saved_voice, load_show_in_recordings, load_voice_commands, save_user_settings,
)
from client.gui.own_keys_panel import AdvancedSection
from client.gui.settings_save import clean_token, submit
from client.gui.theme import DARK_THEME as T
from client.gui.token_dialog import UI as FORM_UI
from client.gui.ui_texts import ui
from client.gui.widgets import TEXT_2, _card, _label

POLL_MS = 100


class CheckRow(QCheckBox):
    """A checkbox whose text wraps: QCheckBox draws its own text on one line,
    too wide for a 440 px window, so the text is a wrapped label beside an
    empty box; clicking the label toggles the box. `text()`/`setText()` keep
    working on the label's words."""

    def __init__(self) -> None:
        super().__init__()
        self.label = _label(size=14)
        self.label.setWordWrap(True)
        self.label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.label.mousePressEvent = lambda _event: self.toggle()  # type: ignore[method-assign]

    def setText(self, text: str) -> None:  # noqa: N802 - Qt's name
        self.label.setText(text)

    def text(self) -> str:
        return self.label.text()

    def row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.label, 1)
        return row


class SettingsPanel(QWidget):
    saved = Signal(str)  # "saved" (voice/options written) or "connected" (a new token accepted and written)

    def __init__(self) -> None:
        super().__init__()
        self.ui_language = "pt"
        self.saved_token = ""
        self.history_was_on = False
        self._result: tuple[str, str] | None = None
        self._worker: threading.Thread | None = None  # not None while a token check is in flight
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self._check_result)

        col = QVBoxLayout(self)
        col.setContentsMargins(20, 6, 20, 0)
        col.setSpacing(10)
        self.title = _label(size=22, weight=600, heading=True)
        col.addWidget(self.title)
        self.intro = _label(name="sub")
        self.intro.setWordWrap(True)
        col.addWidget(self.intro)

        scroll = QScrollArea()
        self.scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget()
        form = QVBoxLayout(host)
        form.setContentsMargins(0, 0, 6, 12)
        form.setSpacing(10)

        # -- language and voice ---------------------------------------------------------
        card, body = self._group()
        self.language_label = self._caps(body)
        self.language_box = QComboBox()
        for code, name in voices.LANGUAGES.items():
            self.language_box.addItem(name, code)
        body.addWidget(self.language_box)
        self.voice_label = self._caps(body)
        voice_row = QHBoxLayout()
        voice_row.setSpacing(8)
        self.voice_box = QComboBox()
        for box in (self.language_box, self.voice_box):  # the window is 440 px: never grow to the longest name
            box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            box.setMinimumContentsLength(12)
        voice_row.addWidget(self.voice_box, 1)
        self.listen_btn = self._button()
        self.listen_btn.clicked.connect(self._listen)
        voice_row.addWidget(self.listen_btn)
        body.addLayout(voice_row)
        form.addWidget(card)

        # -- token -----------------------------------------------------------------------
        card, body = self._group()
        self.token_label = self._caps(body)
        self.field = QLineEdit()
        self.field.returnPressed.connect(self._submit)
        body.addWidget(self.field)
        form.addWidget(card)

        # -- games named by voice (capture.py pass 4) ------------------------------------
        card, body = self._group()
        self.remembered_label = self._caps(body)
        rem_row = QHBoxLayout()
        rem_row.setSpacing(8)
        self.remembered_info = _label(size=14, color=TEXT_2)
        self.remembered_info.setWordWrap(True)
        rem_row.addWidget(self.remembered_info, 1)
        self.forget_btn = self._button()
        self.forget_btn.clicked.connect(self._forget_games)
        rem_row.addWidget(self.forget_btn)
        body.addLayout(rem_row)
        form.addWidget(card)

        # -- the game's volume while Ombri talks (client/ducking.py) ---------------------
        card, body = self._group()
        self.duck_label = self._caps(body)
        self.duck_box = CheckRow()
        body.addLayout(self.duck_box.row())
        self.duck_slider = QSlider(Qt.Orientation.Horizontal)
        self.duck_slider.setRange(1, 8)  # 10% .. 80%, in steps of 10
        body.addWidget(self.duck_slider)
        self.duck_value = _label(size=13, color=TEXT_2)
        body.addWidget(self.duck_value)
        self.duck_box.toggled.connect(self._duck_changed)
        self.duck_slider.valueChanged.connect(self._duck_changed)
        form.addWidget(card)

        # -- one checkbox each ---------------------------------------------------------------
        self.rec_label, self.rec_box = self._check_group(form)
        self.follow_label, self.follow_box = self._check_group(form)
        self.vcmd_label, self.voice_cmd_box = self._check_group(form)
        self.history_label, self.history_box = self._check_group(form)

        # -- Avançado: own keys for Voz / Ouvido, and the Links tab's "Limpar" (2026-11-23)
        self.advanced = AdvancedSection()
        form.addWidget(self.advanced)

        scroll.setWidget(host)
        col.addWidget(scroll, 1)

        # -- status and save, always in reach under the list -----------------------------
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 4, 0, 16)
        foot.setSpacing(12)
        self.status = _label(size=13, color=TEXT_2)
        self.status.setWordWrap(True)
        foot.addWidget(self.status, 1)
        self.save_btn = QPushButton()
        self.save_btn.setObjectName("primary")
        self.save_btn.setFixedHeight(40)
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.clicked.connect(self._submit)
        foot.addWidget(self.save_btn, 0, Qt.AlignmentFlag.AlignBottom)
        col.addLayout(foot)

        self.language_box.currentIndexChanged.connect(self._language_changed)
        # What's picked is what plays (owner, 2026-11-23: Coffee was picked, the
        # "Ouvir" preview played it, but without "Salvar" the app stayed Altair).
        # `activated` = the player's own pick, never a reload.
        self.voice_box.activated.connect(lambda _i: self._save_voice_now())
        self.language_box.activated.connect(lambda _i: self._save_voice_now())
        self.reload()

    # ------------------------------------------------------------------ building blocks
    @staticmethod
    def _group() -> tuple[QFrame, QVBoxLayout]:
        card = _card()
        body = QVBoxLayout(card)
        body.setContentsMargins(16, 12, 16, 14)
        body.setSpacing(8)
        return card, body

    @staticmethod
    def _caps(body: QVBoxLayout) -> QLabel:
        lab = _label(name="caps")
        body.addWidget(lab)
        return lab

    @staticmethod
    def _button() -> QPushButton:
        b = QPushButton()
        b.setObjectName("small")
        b.setFixedHeight(34)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        return b

    def _check_group(self, form: QVBoxLayout) -> tuple[QLabel, CheckRow]:
        card, body = self._group()
        caps = self._caps(body)
        box = CheckRow()
        body.addLayout(box.row())
        form.addWidget(card)
        return caps, box

    # ------------------------------------------------------------------ values
    def show_advanced(self) -> None:
        """Scrolls to Avançado (the own-key notice's "Abrir configurações")."""
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.scroll.ensureWidgetVisible(self.advanced, 0, 0))

    def reload(self) -> None:
        """The saved values into the widgets - on build and each time the view
        opens, so a change made elsewhere (the dialog, play_history) shows."""
        self.saved_token = load_saved_token()
        start_voice = voices.get(load_saved_voice())
        self.ui_language = start_voice.language
        self.language_box.blockSignals(True)
        self.language_box.setCurrentIndex(max(0, self.language_box.findData(start_voice.language)))
        self.language_box.blockSignals(False)
        self._fill_voices(start_voice.key)
        self.field.setText("")
        duck_on, duck_level = load_duck_settings()
        self.duck_box.setChecked(duck_on)
        self.duck_slider.setValue(max(1, min(8, round(duck_level * 10))))
        self.rec_box.setChecked(load_show_in_recordings())
        # The morning report's play history (client/play_history.py). Saved only if
        # changed here: leaving it alone must not read as the player's "no".
        self.history_was_on = _read_user_settings().get("play_history") is True
        self.history_box.setChecked(self.history_was_on)
        self.follow_box.setChecked(load_follow_up_seconds() > 0)
        self.voice_cmd_box.setChecked(load_voice_commands())
        self.status.setText("")
        self.advanced.reload(self.ui_language)
        self._relabel()

    def _s(self, key: str, **kw) -> str:
        text = FORM_UI[self.ui_language][key]
        return text.format(**kw) if kw else text

    def _fill_voices(self, select: str | None = None) -> None:
        self.voice_box.clear()
        for v in voices.for_language(self.ui_language):
            self.voice_box.addItem(voices.label(v, self.ui_language), v.key)
        idx = self.voice_box.findData(select or voices.DEFAULT_VOICE_FOR[self.ui_language])
        self.voice_box.setCurrentIndex(max(0, idx))

    def _relabel(self) -> None:
        s = self._s
        self.title.setText(ui(self.ui_language, "nav_settings"))
        self.intro.setText(s("settings"))
        self.language_label.setText(s("language").upper())
        self.voice_label.setText(s("voice").upper())
        self.token_label.setText(s("token").upper())
        self.listen_btn.setText(s("listen"))
        self.save_btn.setText(s("save"))
        self.field.setPlaceholderText(s("token_keep" if self.saved_token else "token_placeholder"))
        self.remembered_label.setText(s("remembered").upper())
        self.forget_btn.setText(s("forget"))
        n = capture.remembered_count()
        self.remembered_info.setText(s("remembered_count", n=n) if n else s("remembered_none"))
        self.forget_btn.setEnabled(n > 0)
        self.duck_label.setText(s("duck").upper())
        self.duck_box.setText(s("duck_on"))
        self.rec_label.setText(s("rec").upper())
        self.rec_box.setText(s("rec_on"))
        self.follow_label.setText(s("follow").upper())
        self.follow_box.setText(s("follow_on"))
        self.vcmd_label.setText(s("vcmd").upper())
        self.voice_cmd_box.setText(s("vcmd_on"))
        self.history_label.setText(s("history").upper())
        self.history_box.setText(s("history_on"))
        self.advanced.relabel(self.ui_language)
        self._duck_changed()

    def _duck_changed(self, *_args) -> None:
        on = self.duck_box.isChecked()
        self.duck_slider.setEnabled(on)
        self.duck_value.setEnabled(on)
        self.duck_value.setText(self._s("duck_level", n=self.duck_slider.value() * 10))

    def _duck_fields(self) -> dict:
        fields = {"duck_game": self.duck_box.isChecked(), "duck_level": self.duck_slider.value() / 10,
                  "show_in_recordings": self.rec_box.isChecked(), "follow_up": self.follow_box.isChecked(),
                  "voice_commands": self.voice_cmd_box.isChecked()}
        if self.history_box.isChecked() != self.history_was_on:
            fields["play_history"] = self.history_box.isChecked()
        return fields

    def _forget_games(self) -> None:
        capture.forget_remembered_games()
        self._relabel()
        self._set_status(self._s("forgotten"), T.states.listening)

    def _language_changed(self) -> None:
        self.ui_language = self.language_box.currentData()
        self._fill_voices()
        self._relabel()
        self.status.setText("")

    def _save_voice_now(self) -> None:
        voice = self.selected_voice()
        try:
            save_user_settings(voice=voice)
        except OSError:
            return  # "Salvar" still saves it
        self._set_status(self._s("voice_saved", name=voices.get(voice).name), T.states.listening)

    def selected_voice(self) -> str:
        return self.voice_box.currentData() or voices.DEFAULT_VOICE

    def _listen(self) -> None:
        voice = self.selected_voice()

        def play() -> None:
            try:
                from client.fixed_audio import play_preview

                play_preview("sample", voice)
            except Exception:  # noqa: BLE001 - no audio device: nothing to hear, nothing to break
                pass

        threading.Thread(target=play, daemon=True).start()

    # ------------------------------------------------------------------ saving
    def _set_status(self, text: str, color: str) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"font-size: 13px; color: {color};")

    def _submit(self) -> None:
        """settings_save.submit() does the saving (shared with token_dialog); this
        only moves the widgets. A token check's answer lands in `_result` from
        the worker thread and `_poll` reads it on the GUI thread."""
        if self._worker is not None:
            return
        out = submit(self.field.text(), voice=self.selected_voice(), fields=self._duck_fields(),
                     keep_saved=bool(self.saved_token), server_url=load_client_settings().server_url,
                     done=lambda kind, name: setattr(self, "_result", (kind, name)))
        if out.status == "saved":
            self._after_save()
            self._set_status(self._s("saved"), T.states.listening)
            self.saved.emit("saved")
            return
        if out.status == "need_token":
            self._set_status(self._s("need_token"), T.states.thinking)
            return
        self._worker = out.worker
        self.field.setText(clean_token(self.field.text()))
        self.save_btn.setEnabled(False)
        self.field.setEnabled(False)
        self._set_status(self._s("checking"), TEXT_2)
        self._poll.start()

    def _check_result(self) -> None:
        if self._result is None:
            return
        self._poll.stop()
        kind, name = self._result
        self._result = None
        self._worker = None
        self.save_btn.setEnabled(True)
        self.field.setEnabled(True)
        if kind == "ok":  # already written by submit()'s worker, token included
            self.field.setText("")
            self._after_save()
            self._set_status(self._s("connected", name=name), T.states.listening)
            self.saved.emit("connected")
            return
        self.field.setFocus()
        if kind == "rejected":
            self._set_status(self._s("rejected_status"), T.accent)
        else:
            self._set_status(self._s("unreachable"), T.states.thinking)

    def _after_save(self) -> None:
        """What the file says now, so the next save starts from it (the dialog
        closed here; the panel stays open)."""
        self.saved_token = load_saved_token()
        self.history_was_on = _read_user_settings().get("play_history") is True
        self.field.setPlaceholderText(self._s("token_keep" if self.saved_token else "token_placeholder"))
