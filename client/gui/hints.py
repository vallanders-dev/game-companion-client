"""client/gui/hints.py - one-line contextual hints that teach the player what
to do (owner, 2026-11-24): the first time a new thing appears, a line in the
player's language ("Diga \"outra\" pra ver mais, ou \"essa\" pra fixar")
along the bottom of the thing itself, fading on its own, with a "não mostrar
de novo" tick. Each hint shows at most MAX_SHOWS times, then never again;
remembered locally per hint id (`hints.json` next to the pane's files - a
count and a tick, never a question or an answer).

Reusable: `Hints(store_dir).show("pane_open", anchor_widget, lang)` is all a
caller does; the texts live in HINTS (id -> pt/en/es, `{ask}`/`{pad}` filled
from the caller's keys). Who teaches what (2026-11-25, the app-wide pass
after the pane's own three):
- the in-game pane (client/gui/game_pane.py): pane_open, pane_step, pane_pin;
- the chat (client/gui/chat_view.py): chat_links when the first link card
  lands, chat_repeat after the first spoken answer, note_recall when a note
  is kept;
- the window (client/gui/main_window.py): note_recall again on "Minhas
  notas" (same id: one budget), recipe_in_chat when the brewing strip shows
  on the home column with the chat closed, pad_ask when the controller's
  tile is on the home column for the first time.
Every place in one process shares one store per folder (`store_for`), so
the counts never overwrite each other. A hint that teaches something the
screen already says in a fixed label is not a hint (the Build pane's
"Diga próxima camada..." line, the settings' own texts).

Rules: text only, never spoken; never takes focus (the game keeps it); a
fade only with motion on and the window quiet-free (client/gui/motion.py),
else it simply goes after its time; one single-shot timer while it is up and
none while idle; a hint that has been up for less than MIN_UP_MS is not
pushed out by the next one (an answer brings its links and its end within
the same second - the second hint simply waits for a later turn).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QCheckBox, QGraphicsOpacityEffect, QGridLayout, QLabel, QWidget

from client.gui import motion
from client.gui.fonts import BODY_FAMILY, ensure_fonts_loaded

HINTS = {
    "pane_open": {
        "pt": "Diga \"outra\" pra ver mais, ou \"essa\" pra fixar",
        "en": "Say \"another\" to see more, or \"this one\" to pin it",
        "es": "Di \"otra\" para ver más, o \"esa\" para fijarla",
    },
    "pane_step": {
        "pt": "Diga \"próxima camada\" pra avançar, \"volta\" pra voltar",
        "en": "Say \"next layer\" to move on, \"back\" to go back",
        "es": "Di \"siguiente capa\" para avanzar, \"atrás\" para volver",
    },
    "pane_pin": {
        "pt": "Fica na tela enquanto você constrói. \"não, tira\" solta",
        "en": "It stays up while you build. \"no, take it off\" unpins",
        "es": "Se queda mientras construyes. \"no, quita\" la suelta",
    },
    # the chat beside the home column (client/gui/chat_view.py)
    "chat_links": {
        "pt": "Esse link fica na aba Links, só neste PC",
        "en": "This link stays in the Links tab, on this PC only",
        "es": "Este link queda en la pestaña Links, solo en este PC",
    },
    "chat_repeat": {
        "pt": "Não entendeu? Diga \"repete\" com {ask} e eu falo de novo",
        "en": "Missed it? Say \"repeat\" with {ask} and I'll say it again",
        "es": "¿No entendiste? Di \"repite\" con {ask} y lo digo de nuevo",
    },
    # a note kept (the chat) and "Minhas notas" (the window): the morning report reads
    # the player's own notes back - a closed list in server/fixed_replies.py, no model
    "note_recall": {
        "pt": "Pra ouvir suas notas, diga \"me dá o relatório\"",
        "en": "To hear your notes later, say \"give me the report\"",
        "es": "Para oír tus notas después, di \"dame el reporte\"",
    },
    # the home column (client/gui/main_window.py)
    "recipe_in_chat": {
        "pt": "A receita fica no chat, junto da resposta",
        "en": "The recipe stays in the chat, with the answer",
        "es": "La receta queda en el chat, con la respuesta",
    },
    "pad_ask": {  # in the footer line's place, under the tiles (owner 2026-11-25: never over the labels)
        "pt": "Segure {pad} no controle e pergunte",
        "en": "Hold {pad} on the controller to ask",
        "es": "Mantén {pad} en el mando y pregunta",
    },
}
NO_AGAIN = {"pt": "não mostrar de novo", "en": "don't show again", "es": "no mostrar de nuevo"}

MAX_SHOWS = 3  # per hint id, over the player's whole use
SHOW_MS = 7000  # up this long, then gone
MIN_UP_MS = 2500  # a hint up for less than this is not replaced by another
FADE_MS = 600
STORE_NAME = "hints.json"
HEIGHT = 24  # one line; a narrow anchor wraps the text and the strip grows (see HintLabel.place)
MARGIN = 8
MAX_LINES = 3
TICK_BESIDE_MIN = 120  # px of text room beside the tick; under that the tick goes under the text
TEXT_PX, TICK_PX = 11, 10


def strip_font(px: int) -> QFont:
    """The app's own body font at `px` pixels, set in code (not through the
    style sheet): the strip measures its text with the font it draws, on
    every platform - Windows' offscreen Qt has no system font database and
    its placeholder font measures about twice as wide (Claude, 2026-11-25)."""
    ensure_fonts_loaded()
    font = QFont(BODY_FAMILY)
    font.setPixelSize(px)
    return font


class _Keys(dict):
    """`{pad}` with no pad given stays as written rather than raising."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def text_of(hint_id: str, lang: str, **keys) -> str:
    texts = HINTS.get(hint_id) or {}
    text = texts.get(lang) or texts.get("pt") or ""
    return text.format_map(_Keys({k: v for k, v in keys.items() if v})) if text else ""


_STORES: dict[str, "HintStore"] = {}


def store_for(folder: Path | None) -> "HintStore":
    """One store per folder in this process: the pane, the chat and the window
    all count into the same `hints.json`, so no write loses another's count.
    None = a fresh in-memory store (a test)."""
    if folder is None:
        return HintStore(None)
    key = str(Path(folder).resolve())
    store = _STORES.get(key)
    if store is None:
        store = _STORES[key] = HintStore(Path(folder))
    return store


class HintStore:
    """`hints.json`: {hint_id: {"shown": n, "off": bool}}. A missing or broken
    file is an empty store; writes are whole-file and never raise."""

    def __init__(self, folder: Path | None) -> None:
        self.path = (folder / STORE_NAME) if folder is not None else None
        self._data: dict = {}
        if self.path is not None:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
            except (OSError, ValueError):
                self._data = {}

    def entry(self, hint_id: str) -> dict:
        e = self._data.get(hint_id) or {}
        try:
            shown = max(0, int(e.get("shown") or 0))
        except (TypeError, ValueError):
            shown = 0
        return {"shown": shown, "off": bool(e.get("off"))}

    def should_show(self, hint_id: str) -> bool:
        e = self.entry(hint_id)
        return hint_id in HINTS and not e["off"] and e["shown"] < MAX_SHOWS

    def mark_shown(self, hint_id: str) -> None:
        e = self.entry(hint_id)
        self._data[hint_id] = {"shown": e["shown"] + 1, "off": e["off"]}
        self._save()

    def dismiss(self, hint_id: str) -> None:
        e = self.entry(hint_id)
        self._data[hint_id] = {"shown": e["shown"], "off": True}
        self._save()

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass


class HintLabel(QWidget):
    """The line itself: a dark rounded strip along the bottom of its anchor
    with the text and the tick. Child of the anchor, so it is click-through
    whenever the anchor is, and dies with it. One line when the text fits
    beside the tick; a narrow anchor (the smallest in-game pane, the home
    column's cards) wraps the text to two or three lines and, narrower still,
    puts the tick under it - the sentence is the teaching, never cut."""

    def __init__(self, anchor: QWidget, hint_id: str, text: str, lang: str, on_dismiss, inset: int = 0,
                 top: int | None = None) -> None:
        super().__init__(anchor)
        self.hint_id = hint_id
        self.text = text
        self.inset = max(0, int(inset))  # px kept free under the strip (a credit line, a button row)
        self.top = None if top is None else max(0, int(top))  # set: the strip's top edge, not the anchor's bottom
        self._on_dismiss = on_dismiss
        self._tick_beside = True
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.label = QLabel(text, self)
        self.label.setFont(strip_font(TEXT_PX))
        self.label.setStyleSheet("color: #F2F4F8; background: transparent;")
        self.label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tick = QCheckBox(NO_AGAIN.get(lang) or NO_AGAIN["pt"], self)
        self.tick.setFont(strip_font(TICK_PX))
        self.tick.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tick.setStyleSheet("QCheckBox { color: #C9CED8; background: transparent; }"
                                "QCheckBox::indicator { width: 10px; height: 10px; border: 1px solid #C9CED8;"
                                " border-radius: 3px; background: transparent; }"
                                "QCheckBox::indicator:checked { background: #C9CED8; }")
        self.tick.toggled.connect(self._ticked)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(10, 2, 8, 2)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(2)
        self.grid.addWidget(self.label, 0, 0)
        self.grid.addWidget(self.tick, 0, 1)
        self.grid.setColumnStretch(0, 1)
        self._effect: QGraphicsOpacityEffect | None = None
        self.leaving = False  # timed out or ticked: fading, no longer "up"
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.go)

    def _ticked(self, on: bool) -> None:
        if on:
            self._on_dismiss(self.hint_id)
            self.go()

    def _shape(self, w: int) -> tuple[int, int, bool]:
        """(lines, height, tick beside) for a strip `w` px wide."""
        self.label.ensurePolished()  # a window style sheet may still touch the font: measure after it
        self.tick.ensurePolished()
        m = self.grid.contentsMargins()
        fm = self.label.fontMetrics()
        tick_w = self.tick.sizeHint().width()
        beside = w - m.left() - m.right() - self.grid.horizontalSpacing() - tick_w
        tick_beside = beside >= TICK_BESIDE_MIN
        room = beside if tick_beside else w - m.left() - m.right()
        if fm.horizontalAdvance(self.text) <= room:
            lines = 1
        else:
            wrapped = fm.boundingRect(QRect(0, 0, max(40, room), 10_000), Qt.TextFlag.TextWordWrap, self.text)
            lines = min(MAX_LINES, max(2, round(wrapped.height() / max(1, fm.lineSpacing()))))
        body = HEIGHT if lines == 1 else lines * fm.lineSpacing() + m.top() + m.bottom() + 6
        if not tick_beside:
            body += self.tick.sizeHint().height() + self.grid.verticalSpacing()
        return lines, body, tick_beside

    def place(self) -> None:
        a = self.parentWidget()
        if a is None:
            return
        w = max(40, a.width() - 2 * MARGIN)
        lines, h, tick_beside = self._shape(w)
        self.label.setWordWrap(lines > 1)
        if tick_beside != self._tick_beside:
            self._tick_beside = tick_beside
            self.grid.removeWidget(self.label)
            self.grid.removeWidget(self.tick)
            if tick_beside:
                self.grid.addWidget(self.label, 0, 0)
                self.grid.addWidget(self.tick, 0, 1)
            else:  # both span the two columns: the text gets the whole width, the tick sits under it
                self.grid.addWidget(self.label, 0, 0, 1, 2)
                self.grid.addWidget(self.tick, 1, 0, 1, 2, Qt.AlignmentFlag.AlignRight)
        y = self.top if self.top is not None else max(0, a.height() - self.inset - h - MARGIN)
        self.setGeometry(MARGIN, y, w, h)
        self.raise_()

    @property
    def lines(self) -> int:
        return self._shape(max(40, self.width()))[0]

    def start(self, ms: int = SHOW_MS) -> None:
        self.place()
        self.show()
        self.place()  # showing can run the anchor's pending layout (its size) and the style sheet (the font)
        self._timer.start(max(1, ms))

    def go(self) -> None:
        """Leave: a fade when motion allows it, else at once. Deleted after."""
        self._timer.stop()
        self.leaving = True
        if self._effect is not None or not self.isVisible():
            return
        if not motion.enabled() or motion.quiet(self):
            self.hide()
            self.deleteLater()
            return
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)

        def step(v: float) -> None:
            if self._effect is not None:
                self._effect.setOpacity(v)

        def done() -> None:
            self.hide()
            self.deleteLater()

        motion.tween(self, 1.0, 0.0, FADE_MS, step, done)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        r = HEIGHT / 2 if self.height() <= HEIGHT else 12.0  # a pill for one line, a card for more
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), r, r)
        p.fillPath(path, QColor(16, 18, 24, 205))  # dark over a picture ...
        p.setPen(QPen(QColor(255, 255, 255, 56), 1.0))  # ... a thin light edge over the dark glass
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.end()


class _Follow(QObject):
    """Keeps the strip along the anchor's bottom when the anchor resizes."""

    def __init__(self, label: HintLabel) -> None:
        super().__init__(label)
        self.label = label

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show) and not self.label.leaving:
            self.label.place()  # also while the label is still on its way up: the anchor's first layout
        return False


class Hints:
    """One per place that shows hints (the pane). `store_dir` None = in
    memory only (a test). `show()` returns True when the hint went up."""

    def __init__(self, store_dir: Path | None = None) -> None:
        self.store = store_for(store_dir)
        self._up: dict[str, HintLabel] = {}
        self._since = 0.0  # when the hint that is up went up

    def should_show(self, hint_id: str) -> bool:
        return self.store.should_show(hint_id)

    def show(self, hint_id: str, anchor: QWidget, lang: str, ms: int = SHOW_MS, inset: int = 0,
             top: int | None = None, **keys) -> bool:
        """The hint along the bottom of `anchor`, `inset` px above its edge
        (room for a credit line) - or with its top edge at `top` px of the
        anchor (right under a list's last row); `keys` fill `{ask}` / `{pad}`.
        False when it is not due (seen enough, ticked off), has no text, or
        another hint went up less than MIN_UP_MS ago (that one stays; this one
        is not counted). The same hint already up stays up, counted once."""
        if anchor is None or not self.should_show(hint_id):
            return False
        if self.showing:
            if hint_id in self.showing:
                return True
            if (time.monotonic() - self._since) * 1000 < MIN_UP_MS:
                return False
        text = text_of(hint_id, lang, **keys)
        if not text:
            return False
        self.hide_all()
        label = HintLabel(anchor, hint_id, text, lang, self.store.dismiss, inset, top)
        anchor.installEventFilter(_Follow(label))
        label.destroyed.connect(lambda *_: self._up.pop(hint_id, None))
        self._up[hint_id] = label
        self._since = time.monotonic()
        self.store.mark_shown(hint_id)
        label.start(ms)
        return True

    def hide_all(self) -> None:
        """A new picture or a close: whatever is up goes (no fade: it is
        being replaced, not timing out)."""
        for label in list(self._up.values()):
            label.hide()
            label.deleteLater()
        self._up.clear()

    @property
    def showing(self) -> list[str]:
        return [k for k, v in self._up.items() if v.isVisible() and not v.leaving]

    def strip(self, hint_id: str) -> "HintLabel | None":
        """The strip of `hint_id` while it exists (up or fading), else None -
        for a caller that makes room for it (the home column's slot)."""
        return self._up.get(hint_id)
