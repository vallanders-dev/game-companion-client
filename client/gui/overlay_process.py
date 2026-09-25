"""gui/overlay_process.py — python -m gui.overlay_process

The overlay as a standalone process, driven by gui/client.py's JSON lines
on stdin (see that module for why it is a separate process). Exits when
stdin closes, i.e. whenever main.py exits, however it exits.

The window starts hidden and only appears the first time a "game" message
carries a real name - the tray-and-companion idea (2026-09-24): the player
shouldn't see anything until there's actually a game session for it to be
about. Once shown for a session it stays shown (same stickiness as
main.py's own `active_game`, which never reverts to None on a transient
miss - see CLAUDE.md); there's no message to hide it again.

Messages:
  {"type": "state", "state": "idle|ouvindo|pensando|falando", "question"?: str, "answer"?: str}
  {"type": "append", "text": str}      streamed answer piece, as released to TTS
  {"type": "game", "name": str|null}   first non-null name reveals the window
  {"type": "usage", "count": int, "cap": int}
"""

from __future__ import annotations

import json
import signal
import sys
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from client.gui.overlay import DARK_THEME, HudState, OverlayWindow

STATES = {s.value: s for s in HudState}
# The fast lane releases 2-5 character deltas; re-laying out the card on
# every one would restart the resize animation dozens of times a second.
APPEND_FLUSH_MS = 150


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
            print(f"[overlay-process] linha inválida ignorada: {line!r}", flush=True)
    bridge.eof.emit()


def main() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # lifetime is tied to stdin, not Ctrl+C
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    app = QApplication(sys.argv)
    window = OverlayWindow(theme=DARK_THEME)
    revealed = {"value": False}

    current = {"state": HudState.IDLE, "answer": ""}
    pending: list[str] = []
    flush_timer = QTimer()
    flush_timer.setSingleShot(True)
    flush_timer.setInterval(APPEND_FLUSH_MS)

    def flush() -> None:
        if pending and current["state"] is not HudState.IDLE:
            current["answer"] += "".join(pending)
            window.set_state(current["state"], answer=current["answer"])
        pending.clear()

    flush_timer.timeout.connect(flush)

    def handle(msg: dict) -> None:
        kind = msg.get("type")
        if kind == "state":
            state = STATES.get(msg.get("state"))
            if state is None:
                print(f"[overlay-process] estado desconhecido: {msg!r}", flush=True)
                return
            flush()
            if "answer" in msg:
                current["answer"] = msg["answer"] or ""
            current["state"] = state
            if state is HudState.IDLE:
                window.set_state(state)
            else:
                window.set_state(state, question=msg.get("question"), answer=msg.get("answer"))
            # Diagnostic only (2026-09-25) - a player reported the overlay
            # visibly lagging behind the audio. Total pipe latency: from
            # gui.client.HudClient.state() building the message to
            # set_state() returning here (repaint is scheduled, not yet on
            # screen - Qt paints on the next event-loop turn, effectively
            # immediate). Remove once the lag is diagnosed and fixed.
            t_sent = msg.get("t_sent")
            if t_sent is not None:
                print(f"[overlay-process] state={msg.get('state')} pipe_latency={(time.time() - t_sent) * 1000:.1f}ms",
                      flush=True)
        elif kind == "append":
            pending.append(msg.get("text", ""))
            if not flush_timer.isActive():
                flush_timer.start()
        elif kind == "game":
            name = msg.get("name")
            window.set_game(name)
            if name and not revealed["value"]:
                revealed["value"] = True
                window.show()
        elif kind == "usage":
            window.set_usage(int(msg.get("count", 0)), int(msg.get("cap", 0)))

    bridge = _Bridge()
    bridge.message.connect(handle)
    bridge.eof.connect(app.quit)
    threading.Thread(target=_read_stdin, args=(bridge,), name="overlay-stdin", daemon=True).start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
