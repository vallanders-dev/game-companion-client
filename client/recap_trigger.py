"""client/recap_trigger.py — WHEN the end-of-session recap is offered
(docs/player_notes.md, piece C). Pure logic, no Windows calls, so
eval/client_recap_trigger_harness.py can drive it with a fake clock.

The rule the owner approved (2026-10-31): the game's PROCESS has been gone
for `after_seconds` (20 s) while the app keeps running - never "lost
focus", so alt-tabbing to Discord mid-play is nothing. Once per game
session: after an offer, nothing more until that game is seen running
again (a new process). The server decides WHETHER there is anything to say
(`recap_nothing` when the session memory is empty or already offered);
this class only decides when to ask it.

main.py feeds it from the detection loop: `seen(game, pid, exe)` whenever
`detect_game()` matched a window, `tick(alive)` on every poll while a game
is set (alive = `capture.game_process_alive(pid, exe)`).
"""
from __future__ import annotations

import time

RECAP_AFTER_SECONDS = 20.0


class RecapTrigger:
    def __init__(self, after_seconds: float = RECAP_AFTER_SECONDS, clock=time.monotonic) -> None:
        self.after_seconds = after_seconds
        self._clock = clock
        self.game: str | None = None
        self.pid: int | None = None
        self.exe = None
        self._gone_since: float | None = None
        self._offered_pid: int | None = None  # the process an offer already happened for

    def seen(self, game: str, pid: int | None, exe=None) -> None:
        """The game is in front right now (a `detect_game()` match with its
        window's process). Without a pid there is nothing to watch, so the
        call is ignored - a typed/voice switch goes through `forget()`."""
        if not pid:
            return
        if game != self.game or pid != self.pid:
            # A new process of this (or another) game: a new session, a new chance.
            self.game, self.pid, self.exe = game, pid, exe
        self._gone_since = None

    def forget(self) -> None:
        """The player switched games by typing/voice: nothing to watch for the old one."""
        self.game = self.pid = self.exe = None
        self._gone_since = None

    def watching(self) -> bool:
        """True while there is a process to watch (a game set by voice/typing
        without a matched window has none - then there is never an offer)."""
        return bool(self.game and self.pid)

    def tick(self, alive: bool) -> bool:
        """One poll. Returns True exactly once per game process: the moment
        it has been gone for `after_seconds`."""
        if not self.watching():
            return False
        now = self._clock()
        if alive:
            self._gone_since = None
            return False
        if self._gone_since is None:
            self._gone_since = now
            return False
        if now - self._gone_since < self.after_seconds or self._offered_pid == self.pid:
            return False
        self._offered_pid = self.pid
        return True

    def skip(self) -> None:
        """The offer was due but can't happen now (paused): count it as done
        for this process, so it doesn't pop up minutes later on resume."""
        if self.pid:
            self._offered_pid = self.pid
