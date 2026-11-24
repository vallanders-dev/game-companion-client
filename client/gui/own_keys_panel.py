"""client/gui/own_keys_panel.py — the Avançado section of Configurações
(docs/chat_redesign.md, 2026-11-23): the player's own keys for Voz and Ouvido,
Cérebro and Olhos as "Em breve", and the Links tab's "Limpar".

A key is stored only after "Testar" got an answer from the provider (one free
call: the voice list or the model list), encrypted by client/own_keys.py. The
field never shows a stored key back. Turning a card on or off, or picking a
voice, saves at once; client/main.py reads the file between turns.
"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from client import links_store, own_keys, own_speech
from client.gui import motion
from client.gui.ui_texts import ui
from client.gui.widgets import ACCENT, OK_GREEN, TEXT_2, TEXT_3, _card, _label

PROVIDER_NAMES = {"elevenlabs": "ElevenLabs", "openai": "OpenAI"}
# How the providers' key pages name a permission (ElevenLabs > Developers > API Keys).
PERMISSION_NAMES = {"voices_read": "Voices › Read", "text_to_speech": "Text to Speech",
                    "speech_to_text": "Speech to Text", "api.model.read": "Models › Read",
                    "api.model.audio.request": "Model capabilities › Audio"}


def why_text(lang: str, code: str, detail: str = "") -> str:
    """The reason a key didn't work, in the player's words (never the provider's text)."""
    if code == "permission":
        if detail:
            return ui(lang, "adv_why_permission", perm=PERMISSION_NAMES.get(detail, detail))
        return ui(lang, "adv_why_permission_any")
    if code in ("refused", "credit", "limit", "offline"):
        return ui(lang, f"adv_why_{code}")
    return ui(lang, "adv_why_other", detail=detail or "?")


def _small(text: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("small")
    b.setFixedHeight(32)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class KeyCard(QWidget):
    """One category (voice / ears): provider, key, Testar, voice, on/off."""

    tested = Signal(object, object)  # (voices list or None, error text or None) - from the test thread

    def __init__(self, category: str) -> None:
        super().__init__()
        self.category = category
        self.lang = "pt"
        self.keys_path: Path | None = None
        self._busy = False
        self._loading = False
        self.has_key = False
        self.card = _card()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.card)
        body = QVBoxLayout(self.card)
        body.setContentsMargins(16, 12, 16, 14)
        body.setSpacing(8)
        head = QHBoxLayout()
        self.caps = _label(name="caps")
        head.addWidget(self.caps)
        head.addStretch(1)
        self.on_box = QPushButton()
        self.on_box.setObjectName("small")
        self.on_box.setCheckable(True)
        self.on_box.setFixedHeight(28)
        self.on_box.setCursor(Qt.CursorShape.PointingHandCursor)
        self.on_box.toggled.connect(self._on_toggled)
        head.addWidget(self.on_box)
        body.addLayout(head)
        self.sub = _label(size=13, color=TEXT_2)
        body.addWidget(self.sub)

        self.provider = QComboBox()
        for p in own_keys.PROVIDERS[category]:
            self.provider.addItem(PROVIDER_NAMES[p], p)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.provider.setVisible(self.provider.count() > 1)  # one provider: the subtitle names it
        body.addWidget(self.provider)

        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.returnPressed.connect(self.test)
        key_row.addWidget(self.key, 1)
        self.test_btn = _small()
        self.test_btn.clicked.connect(self.test)
        key_row.addWidget(self.test_btn)
        body.addLayout(key_row)

        self.voice = QComboBox()
        self.voice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.voice.setMinimumContentsLength(12)
        self.voice.currentIndexChanged.connect(self._voice_changed)
        self.voice.setVisible(category == "voice")
        body.addWidget(self.voice)

        foot = QHBoxLayout()
        self.status = _label(size=12, color=TEXT_3)
        self.status.setWordWrap(True)
        foot.addWidget(self.status, 1)
        self.forget_btn = _small()
        self.forget_btn.clicked.connect(self.forget)
        foot.addWidget(self.forget_btn)
        body.addLayout(foot)
        self.tested.connect(self._tested)
        self.failed_why: tuple[str, str] | tuple = ()

    # -- values ---------------------------------------------------------------
    def reload(self) -> None:
        state = own_keys.load(self.keys_path)[self.category]
        self._loading = True
        self.provider.setCurrentIndex(max(0, self.provider.findData(state["provider"])))
        self._fill_voices([], state["voice"])
        self.on_box.setChecked(state["on"])
        self.has_key = state["has_key"]
        self._loading = False
        self.key.setText("")
        if self.failed_why:  # (code, detail): it failed in a turn this run (the voice loop told us)
            self._status(ui(self.lang, "adv_failed_live", why=why_text(self.lang, *self.failed_why)), ACCENT)
        else:
            self._status("", TEXT_3)
        self.relabel(self.lang)

    def relabel(self, lang: str) -> None:
        self.lang = lang
        self.caps.setText(ui(lang, f"adv_{self.category}"))
        self.sub.setText(ui(lang, f"adv_{self.category}_sub"))
        self.on_box.setText(ui(lang, "adv_on"))
        self.on_box.setEnabled(self.has_key)
        self.key.setPlaceholderText(ui(lang, "adv_key_kept" if self.has_key else "adv_key"))
        self.test_btn.setText(ui(lang, "adv_testing" if self._busy else "adv_test"))
        self.forget_btn.setText(ui(lang, "adv_forget"))
        self.forget_btn.setVisible(self.has_key)

    def _fill_voices(self, pairs: list, select: str = "") -> None:
        self.voice.blockSignals(True)
        self.voice.clear()
        provider = self.provider.currentData()
        if not pairs and provider == "openai":
            pairs = [(v, v.capitalize()) for v in own_speech.OPENAI_VOICES]
        if not pairs and select:
            pairs = [(select, select)]  # an ElevenLabs id saved earlier; the full list comes with "Testar"
        for vid, name in pairs:
            self.voice.addItem(name, vid)
        self.voice.setCurrentIndex(max(0, self.voice.findData(select)))
        self.voice.blockSignals(False)
        self.voice.setVisible(self.category == "voice" and self.voice.count() > 0)  # ElevenLabs: after "Testar"

    def _save(self, key: str | None = None) -> None:
        own_keys.save(self.category, on=self.on_box.isChecked(), provider=self.provider.currentData(),
                      voice=self.voice.currentData() or "", key=key, path=self.keys_path)

    def _on_toggled(self, _on: bool) -> None:
        if not self._loading:
            self._save()

    def _voice_changed(self, _i: int) -> None:
        if not self._loading and self.has_key:
            self._save()

    def _provider_changed(self, _i: int) -> None:
        if self._loading:
            return
        # A key belongs to one provider: switching asks for that provider's key.
        self._fill_voices([])
        if self.has_key:
            self._loading = True
            self.on_box.setChecked(False)
            self._loading = False
            own_keys.save(self.category, on=False, provider=self.provider.currentData(), key="",
                          path=self.keys_path)
            self.has_key = False
        self.relabel(self.lang)

    # -- Testar ---------------------------------------------------------------
    def test(self) -> None:
        if self._busy:
            return
        key = self.key.text().strip()
        if not key:
            self._status(ui(self.lang, "adv_need_key"), ACCENT)
            return
        self._busy = True
        self.relabel(self.lang)
        self.key.setEnabled(False)
        category, provider = self.category, self.provider.currentData()

        def work() -> None:
            try:
                pairs = own_speech.test_key(category, provider, key)
            except own_speech.OwnSpeechError as exc:
                self.tested.emit(None, (exc.code, exc.detail))
                return
            except Exception:  # noqa: BLE001 - never a key in a traceback
                self.tested.emit(None, ("other", ""))
                return
            self.tested.emit(pairs, None)

        threading.Thread(target=work, name="own-key-test", daemon=True).start()

    def _tested(self, pairs, error) -> None:
        self._busy = False
        self.key.setEnabled(True)
        if error is not None:
            self._status(ui(self.lang, "adv_failed", why=why_text(self.lang, *error)), ACCENT)
            motion.fade_in(self.status, rise=0)
            self.relabel(self.lang)
            return
        if self.category == "voice":
            self._fill_voices(pairs or [], self.voice.currentData() or "")
        try:
            self._loading = True
            self.on_box.setChecked(True)
            self._loading = False
            self._save(key=self.key.text())
        except OSError:
            self._status(ui(self.lang, "adv_no_dpapi"), ACCENT)
            self.relabel(self.lang)
            return
        self.has_key = True
        self.failed_why = ()  # a new key that works: the warning goes
        self.key.setText("")
        self._status(ui(self.lang, "adv_ok"), OK_GREEN)
        self.relabel(self.lang)

    def forget(self) -> None:
        own_keys.save(self.category, on=False, provider=self.provider.currentData(), key="", path=self.keys_path)
        self.has_key = False
        self._loading = True
        self.on_box.setChecked(False)
        self._loading = False
        self.failed_why = ()
        self._status(ui(self.lang, "adv_forgotten"), TEXT_2)
        self.relabel(self.lang)

    def _status(self, text: str, color: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))
        self.status.setStyleSheet(f"font-size: 12px; color: {color};")


class SoonCard(QWidget):
    def __init__(self, category: str) -> None:
        super().__init__()
        self.category = category
        card = _card()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 12, 16, 12)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.caps = _label(name="caps")
        self.sub = _label(size=13, color=TEXT_3)
        col.addWidget(self.caps)
        col.addWidget(self.sub)
        row.addLayout(col, 1)
        self.pill = _label(size=12, color=TEXT_3)
        row.addWidget(self.pill)

    def relabel(self, lang: str) -> None:
        self.caps.setText(ui(lang, f"adv_{self.category}"))
        self.sub.setText(ui(lang, f"adv_{self.category}_sub"))
        self.pill.setText(ui(lang, "adv_soon"))


class AdvancedSection(QWidget):
    links_cleared = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.lang = "pt"
        self.links_path: Path | None = None
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 8, 0, 0)
        col.setSpacing(10)
        self.title = _label(name="caps")
        col.addWidget(self.title)
        self.intro = _label(size=13, color=TEXT_2)
        self.intro.setWordWrap(True)
        col.addWidget(self.intro)
        self.cards = {c: KeyCard(c) for c in own_keys.CATEGORIES}
        for c in self.cards.values():
            col.addWidget(c)
        self.soon = [SoonCard("brain"), SoonCard("eyes")]
        for c in self.soon:
            col.addWidget(c)
        self.fallback = _label(size=12, color=TEXT_3)
        self.fallback.setWordWrap(True)
        col.addWidget(self.fallback)

        links = _card()
        lrow = QHBoxLayout(links)
        lrow.setContentsMargins(16, 12, 12, 12)
        lcol = QVBoxLayout()
        lcol.setSpacing(2)
        self.links_caps = _label(name="caps")
        self.links_info = _label(size=13, color=TEXT_2)
        lcol.addWidget(self.links_caps)
        lcol.addWidget(self.links_info)
        lrow.addLayout(lcol, 1)
        self.links_btn = _small()
        self.links_btn.clicked.connect(self.clear_links)
        lrow.addWidget(self.links_btn)
        col.addWidget(links)

    def mark_failed(self, category: str, code: str, detail: str = "") -> None:
        """Kept as (code, detail), so the card says it in its own language."""
        card = self.cards.get(category)
        if card is not None:
            card.failed_why = (code, detail)
            card._status(ui(card.lang, "adv_failed_live", why=why_text(card.lang, code, detail)), ACCENT)

    @property
    def keys_path(self) -> Path | None:
        return self.cards["voice"].keys_path

    @keys_path.setter
    def keys_path(self, path: Path | None) -> None:
        for c in self.cards.values():
            c.keys_path = path

    def reload(self, lang: str) -> None:
        self.lang = lang
        for c in self.cards.values():
            c.lang = lang
            c.reload()
        self.relabel(lang)

    def relabel(self, lang: str) -> None:
        self.lang = lang
        self.title.setText(ui(lang, "adv_title"))
        self.intro.setText(ui(lang, "adv_intro"))
        self.fallback.setText(ui(lang, "adv_fallback"))
        for c in self.cards.values():
            c.relabel(lang)
        for c in self.soon:
            c.relabel(lang)
        self.links_caps.setText(ui(lang, "links_title"))
        n = len(links_store.load(self.links_path))
        self.links_info.setText(ui(lang, "links_count", n=n) if n else ui(lang, "links_none_saved"))
        self.links_btn.setText(ui(lang, "links_clear"))
        self.links_btn.setEnabled(n > 0)

    def clear_links(self) -> None:
        links_store.clear(self.links_path)
        self.relabel(self.lang)
        self.links_info.setText(ui(self.lang, "links_cleared"))
        self.links_cleared.emit()

