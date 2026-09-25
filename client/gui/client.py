"""client/gui/client.py — main.py's side of the overlay. No Qt imports.

The overlay runs as its OWN process (client/gui/overlay_process.py), fed JSON
lines on stdin, rather than as a Qt event loop inside main.py: main.py's
streamed turn is timing-sensitive (AudioOut's 100 ms sliced writes, the
2026-09-15 teardown segfault), a second event loop competing for the GIL
there is a risk with no upside, and a crash in the overlay must never take
the voice loop down with it.

Every method is fire-and-forget: messages go on a queue drained by a
daemon writer thread, so a slow or dead overlay can never block or raise
into the voice loop. If the overlay process is gone, the client quietly
disables itself (its traceback, if any, is in output/overlay.log).
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # client/gui/client.py -> repo root


class HudClient:
    def __init__(self, enabled: bool, log_path: Path | None = None) -> None:
        self.enabled = False
        self.error: str | None = None
        self._queue: queue.Queue = queue.Queue()
        if not enabled:
            return
        try:
            if log_path is not None:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log = open(log_path, "a", encoding="utf-8")
            else:
                log = subprocess.DEVNULL
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0  # Ctrl+C stays main.py's
            self._proc = subprocess.Popen(
                [sys.executable, "-m", "client.gui.overlay_process"],
                cwd=ROOT, stdin=subprocess.PIPE, stdout=log, stderr=log, creationflags=flags,
            )
        except OSError as exc:
            self.error = str(exc)
            return
        self.enabled = True
        threading.Thread(target=self._writer, name="hud-writer", daemon=True).start()

    def _writer(self) -> None:
        while True:
            msg = self._queue.get()
            try:
                self._proc.stdin.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
                self._proc.stdin.flush()
            except (OSError, ValueError):
                self.enabled = False
                return

    def _send(self, msg: dict) -> None:
        if self.enabled:
            self._queue.put(msg)

    def close(self) -> None:
        """Ends the overlay process cleanly (closes its stdin - the same
        EOF it already exits on when main.py itself exits normally).

        **Added 2026-09-25** after finding the real cause of a live
        UpdateLayeredWindowIndirect ("invalid parameter") spam report: with
        no close(), a caller that creates more than one HudClient in the
        same process (main.py's cmd_ask() reassigns the module-level `_HUD`
        every call, and eval/game_detect_harness.py's PART 3 calls
        cmd_ask() once per scenario) leaves every EARLIER overlay_process
        subprocess running forever - Python garbage-collecting a Popen
        object does not terminate its child. Confirmed directly on this
        machine: repeated test runs left FOUR orphaned `gui.demo` processes
        alive simultaneously, all painting overlapping windows at the same
        screen corner and all fighting over the same HWND_TOPMOST slot -
        exactly the kind of concurrent layered-window updates that could
        produce that error. A single clean instance, verified over 15s of
        real state-cycling with the real Qt/Windows platform (not the
        offscreen one used elsewhere in this project's testing - it never
        exercises the real compositing path at all), produced zero errors.
        Call this before dropping a HudClient reference whenever another
        one might be created in the same process."""
        if not self.enabled:
            return
        self.enabled = False
        try:
            self._proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._proc.terminate()

    def state(self, state: str, question: str | None = None, answer: str | None = None) -> None:
        """state: "idle" | "ouvindo" | "pensando" | "falando". question/answer
        None = leave that caption as it is; "" = clear it.

        Carries a wall-clock send timestamp (2026-09-25, diagnostic only -
        a player reported the overlay visibly lagging behind the audio) so
        gui/overlay_process.py can log total pipe latency (queue -> writer
        thread -> subprocess stdin -> Qt signal -> set_state()) in
        output/overlay.log. Same machine, same clock - time.time() deltas
        are reliable at the tens-of-ms scale this measures."""
        msg: dict = {"type": "state", "state": state, "t_sent": time.time()}
        if question is not None:
            msg["question"] = question
        if answer is not None:
            msg["answer"] = answer
        self._send(msg)

    def append_answer(self, text: str) -> None:
        """A piece of the streamed answer, exactly as released to TTS."""
        if text:
            self._send({"type": "append", "text": text})

    def game(self, name: str | None) -> None:
        self._send({"type": "game", "name": name})

    def usage(self, count: int, cap: int) -> None:
        self._send({"type": "usage", "count": count, "cap": cap})
