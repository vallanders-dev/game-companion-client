"""client/gui/client.py — main.py's side of the desktop UI. No Qt imports.

The UI (in-game orb, main window, tray, notices - 2026-09-29) runs as its OWN
process (client/gui/ui_process.py), fed JSON lines on stdin and answering
with command lines on stdout (quit, pause, settings, ...), rather than as a
Qt event loop inside main.py: main.py's
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
    def __init__(self, enabled: bool, log_path: Path | None = None, on_command=None,
                 start_in_tray: bool = False) -> None:
        """`on_command(name)` is called, on a reader thread, for each command
        the UI sends back ("quit", "pause", "resume", "relaunch_admin",
        "notes_list", "notes_delete:<id>", ...). `start_in_tray`:
        don't open the main window at start (a relaunch while the game is in
        front)."""
        self.enabled = False
        self.error: str | None = None
        self._queue: queue.Queue = queue.Queue()
        self._on_command = on_command
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
                [sys.executable, "-m", "client.gui.ui_process"] + (["--tray"] if start_in_tray else []),
                cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, creationflags=flags,
            )
        except OSError as exc:
            self.error = str(exc)
            return
        self.enabled = True
        threading.Thread(target=self._writer, name="hud-writer", daemon=True).start()
        threading.Thread(target=self._reader, name="hud-reader", daemon=True).start()

    @property
    def pid(self) -> int | None:
        """The UI process's pid while it runs (capture.py masks its windows)."""
        proc = getattr(self, "_proc", None)
        return proc.pid if self.enabled and proc is not None else None

    def _reader(self) -> None:
        for raw in self._proc.stdout:
            try:
                cmd = json.loads(raw.decode("utf-8")).get("cmd")
            except (ValueError, AttributeError):
                continue
            if cmd and self._on_command is not None:
                try:
                    self._on_command(str(cmd))
                except Exception:  # noqa: BLE001 - a bad handler never kills the reader
                    pass

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
        cmd_ask() once per scenario) leaves every EARLIER UI process
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
        the UI process can log total pipe latency (queue -> writer
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

    def voice_level(self, level: float) -> None:
        """How loud the voice is right now, 0..1 (the main window's orb follows it)."""
        self._send({"type": "voice", "level": round(level, 3)})

    def game(self, name: str | None) -> None:
        self._send({"type": "game", "name": name})

    def usage(self, count: int, cap: int) -> None:
        self._send({"type": "usage", "count": count, "cap": cap})

    def connection(self, state: str) -> None:
        """"connecting" | "ok" | "offline"."""
        self._send({"type": "conn", "state": state})

    def info(self, lang: str, voice: str, keys: dict) -> None:
        """UI language, the voice's label and the hotkeys ({"ask", "note", "pad"})."""
        self._send({"type": "info", "lang": lang, "voice": voice, "keys": keys})

    def paused(self, on: bool) -> None:
        self._send({"type": "paused", "on": on})

    def notice(self, kind: str, **extra) -> None:
        """A pop-up above the tray: "admin" (with a relaunch button),
        "offline", "update" (version=..., with a restart button) or
        "announce" (text=..., a message from the server)."""
        self._send({"type": "notice", "kind": kind, **extra})

    def show(self) -> None:
        self._send({"type": "show"})

    def features(self, features) -> None:
        """What the server can do (auth_ok.features): "notes" turns the
        "Minhas notas" page on. Sent after each connect."""
        self._send({"type": "features", "list": sorted(str(f) for f in features)})

    def games(self, names: list[str]) -> None:
        """The answer to the UI's "games_list" command: names for the game
        picker's list (typing any other name still works)."""
        self._send({"type": "games", "list": [str(n) for n in names]})

    def notes(self, items: list[dict], error: str = "") -> None:
        """The answer to the UI's "notes_list" / "notes_delete:<id>" commands:
        the tester's notes, newest first ({id, game, note, created, source}),
        or an error line to show instead."""
        self._send({"type": "notes", "items": list(items), "error": error})

    # -- the Chat view (docs/agentic_chat.md, phase 1, 2026-11-21) -----------
    # Memory only on the UI side: the window shows this run's turns and
    # writes nothing. The texts are what the server already sends per turn.
    def chat_question(self, text: str, game: str | None = None) -> None:
        """The turn's question (turn_meta), opening a new turn in the chat."""
        if text:
            self._send({"type": "chat_question", "text": text, "game": game})

    def chat_answer(self, text: str) -> None:
        """A piece of the answer (turn_text), appended to the newest turn."""
        if text:
            self._send({"type": "chat_answer", "text": text})

    # -- the Build pane (server/build_guide.py, 2026-11-22) ------------------
    def guide(self, body: dict) -> None:
        """The server's `guide` message (or the same shape built from a REST
        click-open): action "open" | "layer" | "close" | "list" with the
        pane's next state. The pane keeps the state in memory; the window
        answers with "guide_state:<id>:<layer>" (or ":list") so the next
        turn_start carries it."""
        if isinstance(body, dict) and body.get("action"):
            self._send({"type": "guide", **{k: v for k, v in body.items() if k != "type"}})

    def guide_assets(self, guide_id: str, icons: dict | None = None, shots: dict | None = None) -> None:
        """The Build pane's pictures as they land (client/build_assets.py,
        2026-11-24): `icons` {block id: base64 PNG} for the chips, `shots`
        {"layer_3" | "front" | ...: base64 JPEG} - the real screenshots of
        `guide_id`'s steps and finished build. Sent several times per open."""
        if guide_id and (icons or shots):
            self._send({"type": "guide_assets", "id": str(guide_id), "icons": dict(icons or {}), "shots": dict(shots or {})})

    def chat_cards(self, cards: list[dict]) -> None:
        """Cards under the newest answer: [{kind, url, title, site, thumb}]."""
        self._send({"type": "chat_cards", "cards": [c for c in cards if isinstance(c, dict)]})

    # -- the brewing strip (server/brewing.py, 2026-11-24) -------------------
    def visual(self, body: dict) -> None:
        """The server's `visual` message (client/gui/game_pane.py; the owner's
        visual-pane brief, 2026-11-24): action "open" (items: image as base64,
        title, site, author, source_url, kind "idea" | "step"; index; query;
        style) | "index" | "pin" | "unpin" | "back" | "show" | "close". The
        in-game pane keeps the state; the UI answers with "pane_state:<json>"
        so the next turn_start.visual says what is on screen."""
        if isinstance(body, dict) and (body.get("action") or body.get("items")):
            self._send({"type": "visual", **{k: v for k, v in body.items() if k != "type"}})

    def pane(self, action: str) -> None:
        """The in-game pane itself, whatever it shows: "back" folds it into
        the orb (content kept), "show" unfolds it, "close" closes it."""
        if action in ("back", "show", "close"):
            self._send({"type": "pane", "action": action})

    def recipe(self, card: dict, icons: dict | None = None) -> None:
        """A `recipe` card (kind "recipe": steps, result, modifiers) for the
        strip on the Ombri view and under the answer in the chat. `icons`:
        {icon id: base64 PNG} main.py already has in its cache; the rest
        follow with recipe_icons() once fetched."""
        if isinstance(card, dict) and card.get("kind") == "recipe":
            self._send({"type": "recipe", "card": card, "icons": dict(icons or {})})

    def recipe_icons(self, icons: dict) -> None:
        """Icons fetched after the strip was shown: {icon id: base64 PNG}."""
        if icons:
            self._send({"type": "recipe_icons", "icons": dict(icons)})

    # -- the chat redesign (docs/chat_redesign.md, 2026-11-23) ---------------
    def chat_stage(self, stage: str) -> None:
        """A stage only the client knows ("screen": a screenshot went with the turn)."""
        self._send({"type": "chat_stage", "stage": stage})

    def chat_done(self, outcome: str) -> None:
        """The turn ended; "failed" when the connection dropped."""
        self._send({"type": "chat_done", "outcome": outcome})

    def chat_confirm(self, text: str) -> None:
        """A typed note: the chat shows "Sim, guardar" / "Não" under it."""
        self._send({"type": "chat_confirm", "text": text})
