"""client/chat_inbox.py — what the PC chat sends to the voice loop (docs/chat_redesign.md,
2026-11-23). No Qt imports.

The chat runs in the UI process; its messages arrive on HudClient's reader thread
as commands (`chat_send:{json}`, `chat_mic`, `chat_confirm:yes|no`). The voice
loop waits on a list of triggers (capture.prompt_scene, the barge-in watcher), so
the inbox duck-types one: `pressed()` is true while a typed message waits, and
`clear()` does nothing - a loop clearing its triggers before a wait must never
throw away a message the player already sent. `take()` is the only way out.

`mic` is a plain trigger: the chat's mic button, answered exactly like F8.
`wait_confirm()` is the typed note's "Sim, guardar" / "Não": it returns the
reply as text the server reads without STT ("reply_text").
"""
from __future__ import annotations

import json
import queue
import threading
from collections import deque

from client.voice_commands import VoiceTrigger

MESSAGE_MAX = 500  # the chat box caps it too (client/gui/chat_view.py MESSAGE_MAX)
QUEUE_MAX = 5  # typed messages waiting while a turn runs; more are dropped
CONFIRM_WAIT_SECONDS = 80.0  # under the server's REPLY_WAIT_SECONDS (90)
# What the buttons send: words server/session.classify_confirmation() reads as a
# clear yes / no in every language the app speaks.
CONFIRM_WORDS = {"pt": ("sim", "não"), "en": ("yes", "no"), "es": ("sí", "no")}


class ChatInbox:
    def __init__(self) -> None:
        self.name = "chat"
        self.hotkey = "chat"
        self.mic = VoiceTrigger("chat_mic")
        self._messages: deque[tuple[str, bool]] = deque()
        self._lock = threading.Lock()
        self._confirm: queue.Queue[bool] = queue.Queue()
        # Called when a typed message is refused (too many waiting): the chat's
        # row for it must not keep "thinking" forever. Set by main.py.
        self.on_dropped = None

    # -- the trigger side (the voice loop) ------------------------------------
    def pressed(self) -> bool:
        with self._lock:
            return bool(self._messages)

    def clear(self) -> None:
        """Not a reset: see the module docstring."""

    def take(self) -> tuple[str, bool] | None:
        """(text, remember) of the oldest typed message, or None."""
        with self._lock:
            return self._messages.popleft() if self._messages else None

    def wait_confirm(self, language: str, cancel: threading.Event | None = None,
                     timeout: float = CONFIRM_WAIT_SECONDS) -> str | None:
        """The typed note's answer as a word ("sim"/"não"), or None when the
        player didn't answer in time (the server then keeps nothing)."""
        while not self._confirm.empty():  # a stale click from an earlier card
            self._confirm.get_nowait()
        yes, no = CONFIRM_WORDS.get(language, CONFIRM_WORDS["en"])
        waited = 0.0
        while waited < timeout:
            if cancel is not None and cancel.is_set():
                return None
            try:
                return yes if self._confirm.get(timeout=0.2) else no
            except queue.Empty:
                waited += 0.2
        return None

    # -- the UI side (HudClient's reader thread) ------------------------------
    def command(self, cmd: str) -> bool:
        """Handles a chat command from the UI; False when it isn't one."""
        if cmd.startswith("chat_send:"):
            try:
                body = json.loads(cmd.removeprefix("chat_send:"))
            except ValueError:
                return True
            if isinstance(body, dict) and not self.put(str(body.get("text") or ""), bool(body.get("remember"))):
                if self.on_dropped is not None:
                    self.on_dropped()
            return True
        if cmd == "chat_mic":
            self.mic.fire()
            return True
        if cmd in ("chat_confirm:yes", "chat_confirm:no"):
            self._confirm.put(cmd.endswith("yes"))
            return True
        return False

    def put(self, text: str, remember: bool = False) -> bool:
        text = " ".join(text.split())[:MESSAGE_MAX]
        if not text:
            return False
        with self._lock:
            if len(self._messages) >= QUEUE_MAX:
                return False
            self._messages.append((text, remember))
        return True
