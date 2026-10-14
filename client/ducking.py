"""Lower the game's volume while Ombri speaks (owner, 2026-10-09).

Ombri can't play louder than 100% of the system volume (boosting samples
distorts), so the GAME goes down instead, like Discord's attenuation: the
game's own Windows audio session (Core Audio ISimpleAudioVolume via pycaw)
drops to `level` x its volume from Ombri's first spoken sample until shortly
after the last one, then goes back. Nothing inside the game is touched.

Which sessions: every audio session whose process has the same exe name as
the foreground window when Ombri starts speaking - that's the game the
player pressed F8 in. Skipped when the foreground isn't a game (Ombri's own
window, a browser, Discord...: capture._not_a_game_exe) and never Ombri's
own process.

Safety:
- The original volume is written to disk BEFORE ducking
  (%APPDATA%/Parca/ducked.json) and put back at the next start, so a crash or
  the tray's abrupt "Sair" mid-answer never leaves a game quiet.
- If the player moves the game's volume during an answer, it isn't restored
  over their choice (only restored while it still sits where we put it).
- All Core Audio calls run on one worker thread (COM initialised there), so
  the audio path never waits on them; any failure just means no ducking.

Settings (settings window, %APPDATA%/Parca/settings.json): duck_game (default
on) and duck_level (default 0.3). DUCK_GAME=false in client/.env turns it off.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from pathlib import Path

from client import config

STATE_PATH = config.USER_SETTINGS_DIR / "ducked.json"
RESTORE_DELAY_SECONDS = 0.6  # back-to-back lines (filler -> answer -> "Anotado") don't pump the volume
_TOLERANCE = 0.02  # "still where we put it"
_OWN_EXES = {"python.exe", "pythonw.exe"}


class _Backend:
    """The real Core Audio side. Separate so the harness can swap it."""

    def init_thread(self) -> None:
        import comtypes

        comtypes.CoInitialize()

    def sessions(self, exe_name: str) -> list:
        from pycaw.pycaw import AudioUtilities

        out = []
        for s in AudioUtilities.GetAllSessions():
            pid = s.ProcessId
            if not pid or pid == os.getpid():
                continue
            try:
                name = s.Process.name() if s.Process else ""
            except Exception:  # noqa: BLE001 - process gone
                continue
            if name.lower() == exe_name.lower():
                out.append((pid, s.SimpleAudioVolume))
        return out

    def foreground_game_exe(self) -> str | None:
        from client import capture

        exe = capture.foreground_exe_path()
        if exe is None or exe.name.lower() in _OWN_EXES or capture._not_a_game_exe(exe):
            return None
        return exe.name


class GameDucker:
    def __init__(self, backend=None, state_path: Path = STATE_PATH,
                 settings=config.load_duck_settings) -> None:
        self.backend = backend or _Backend()
        self.state_path = Path(state_path)
        self._settings = settings
        self._q: queue.Queue = queue.Queue()
        self._ducked: list[tuple[int, object, float, float]] = []  # pid, volume iface, original, ducked-to
        self._ducked_exe = ""
        self._speaking = 0  # how many AudioOuts are mid-speech
        self._lock = threading.Lock()
        self._restore_at: float | None = None
        threading.Thread(target=self._run, name="game-ducker", daemon=True).start()

    # -- called from any thread (speech.speaking_hook) -------------------------
    def speaking(self, started: bool) -> None:
        with self._lock:
            self._speaking = max(0, self._speaking + (1 if started else -1))
            if started:
                self._restore_at = None
                if self._speaking == 1:
                    self._q.put("duck")
            elif self._speaking == 0:
                self._restore_at = time.monotonic() + RESTORE_DELAY_SECONDS
                self._q.put("tick")

    def restore_leftover(self) -> None:
        """At startup: a game left quiet by a crash/abrupt exit gets its volume back."""
        self._q.put("leftover")

    def restore_now(self, timeout: float = 2.0) -> None:
        """Before exiting: put the game's volume back right away."""
        self._q.put("restore")
        self.flush(timeout)

    def flush(self, timeout: float = 2.0) -> None:
        """Wait until every queued command ran (tests, and before exiting)."""
        done = threading.Event()
        self._q.put(done)
        done.wait(timeout)

    # -- worker ------------------------------------------------------------------
    def _run(self) -> None:
        try:
            self.backend.init_thread()
        except Exception:  # noqa: BLE001 - no COM, no ducking
            pass
        while True:
            try:
                cmd = self._q.get(timeout=0.1)
            except queue.Empty:
                cmd = "tick"
            try:
                if isinstance(cmd, threading.Event):
                    cmd.set()
                elif cmd == "duck":
                    self._duck()
                elif cmd == "restore":
                    if self._ducked:
                        self._restore()
                elif cmd == "leftover":
                    self._restore_from_file()
                elif cmd == "tick":
                    with self._lock:
                        due = self._restore_at is not None and time.monotonic() >= self._restore_at
                        if due:
                            self._restore_at = None
                    if due:
                        self._restore()
            except Exception as exc:  # noqa: BLE001 - ducking is best-effort
                print(f"  (abaixar o jogo: {exc})", flush=True)

    def _duck(self) -> None:
        if self._ducked:
            return  # already down (back-to-back lines)
        if self.state_path.exists():
            self._restore_from_file()  # a leftover from a previous run first
        enabled, level = self._settings()
        if not enabled:
            return
        exe = self.backend.foreground_game_exe()
        if not exe:
            return
        targets = []
        for pid, vol in self.backend.sessions(exe):
            original = float(vol.GetMasterVolume())
            targets.append((pid, vol, original, round(original * level, 3)))
        if not targets:
            return
        self._write_state(exe, targets)  # on disk BEFORE anything changes
        for _, vol, _, to in targets:
            vol.SetMasterVolume(to, None)
        self._ducked, self._ducked_exe = targets, exe

    def _restore(self) -> None:
        for _, vol, original, to in self._ducked:
            try:
                if abs(float(vol.GetMasterVolume()) - to) <= _TOLERANCE:
                    vol.SetMasterVolume(original, None)
            except Exception:  # noqa: BLE001 - the game closed meanwhile
                pass
        self._ducked, self._ducked_exe = [], ""
        self._clear_state()

    def _restore_from_file(self) -> None:
        """Windows remembers an app's volume across launches, so a game left
        quiet stays quiet even after a restart: restore any session of the
        saved exe still sitting where we put it. The file is kept until such
        a session is seen (the game may not be running right now)."""
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        exe = data.get("exe", "")
        saved = data.get("sessions", [])
        found = self.backend.sessions(exe) if exe and saved else []
        for _, vol in found:
            cur = float(vol.GetMasterVolume())
            for e in saved:
                if abs(cur - float(e["to"])) <= _TOLERANCE:
                    vol.SetMasterVolume(float(e["original"]), None)
                    break
        if found or not saved:
            self._clear_state()

    def _write_state(self, exe: str, targets) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"exe": exe, "sessions": [
                {"pid": pid, "original": original, "to": to} for pid, _, original, to in targets]}), encoding="utf-8")
            tmp.replace(self.state_path)
        except OSError:
            pass

    def _clear_state(self) -> None:
        try:
            self.state_path.unlink()
        except OSError:
            pass
