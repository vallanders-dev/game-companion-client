"""client/fixed_audio.py — playback of the shipped fixed-line WAVs.

`play_line()` speaks one baked line (`client/fixed_lines.py`) through its own
short-lived `AudioOut`; `FillerPlayer` is the original app's filler logic
(`main.FillerPlayer`), unchanged in behavior, fed from the baked filler WAVs
instead of per-install synthesis - this process has no API key to synthesize
with. Fixed lines are not barge-in-able, same as in the original app.
"""
from __future__ import annotations

import random
import threading
import time

from client.fixed_lines import asset_path
from client.listen import play_stop_tone
from client.speech import AudioOut, read_pcm_wav


def load_line(name: str, voice: str = "raquel") -> bytes | None:
    path = asset_path(name, voice)
    if not path.exists():
        return None
    try:
        return read_pcm_wav(path)
    except Exception:  # noqa: BLE001 - a bad asset just means "not baked"
        return None


def play_line(name: str, voice: str = "raquel", *, fallback_tone: bool = True) -> bool:
    """Speaks a baked line, blocking until it finishes. Returns False if the
    asset is missing/unreadable - then plays the local stop tone instead
    when ``fallback_tone`` (the original app's fallback for an uncached
    fixed line), so a player not watching the console still hears something."""
    pcm = load_line(name, voice)
    if pcm is None:
        if fallback_tone:
            play_stop_tone()
        return False
    out = AudioOut()
    try:
        out.open()
        out.write(pcm)
    except Exception:  # noqa: BLE001 - no output device: the text was already printed
        out.close(drain=False)
        return False
    out.close(drain=True)
    return True


def load_fillers(voice: str = "raquel") -> list[bytes]:
    fillers = []
    n = 1
    while (pcm := load_line(f"filler_{n}", voice)) is not None:
        fillers.append(pcm)
        n += 1
    return fillers


class FillerPlayer:
    """Plays one filler if the answer audio hasn't started ``delay_s`` after
    arming - and never otherwise. Ported from the original app's
    ``main.FillerPlayer``: the decision is made under ``AudioOut.lock``, the
    same lock the first answer frame takes (see ``mark_answer_started()``),
    so a filler can never start after the answer or overlap it - the loser
    either skips (filler) or waits on ``filler_done`` (answer). A filler
    that started is not cut; it's ~1 s long. Rotates without repeating the
    previous line."""

    def __init__(self, fillers: list[bytes], delay_s: float) -> None:
        self._fillers = fillers
        self._delay = delay_s
        self._last = -1
        self._rng = random.Random()

    @property
    def enabled(self) -> bool:
        return bool(self._fillers) and self._delay >= 0

    def arm(self, out: AudioOut, cancel: threading.Event) -> threading.Timer:
        choices = [i for i in range(len(self._fillers)) if i != self._last] or [0]
        idx = self._rng.choice(choices)
        self._last = idx
        return play_before_answer(out, cancel, self._fillers[idx], self._delay)


def play_before_answer(out: AudioOut, cancel: threading.Event, pcm: bytes, delay_s: float) -> threading.Timer:
    """Plays `pcm` after `delay_s` unless the answer has started by then -
    the filler rule, shared by the fillers and the web-search lines. The
    decision is taken under ``AudioOut.lock`` (see ``mark_answer_started()``),
    so a line can never start after or over the answer; a line due while
    another one is still playing waits for it rather than overlapping."""

    def fire() -> None:
        if cancel.is_set():
            return
        # Never overlap/precede the 440 Hz stop-recording cue, which plays
        # through sounddevice's convenience stream (see the original app).
        try:
            import sounddevice as sd

            sd.wait()
        except Exception:  # noqa: BLE001
            pass
        while True:
            with out.lock:
                if out.answer_started.is_set() or cancel.is_set() or not out.is_open:
                    return
                if not out.filler_in_progress:
                    out.filler_in_progress = True
                    out.filler_done.clear()
                    break
            time.sleep(0.05)
        try:
            out.write(pcm, cancel)
        finally:
            with out.lock:
                out.filler_in_progress = False
            out.filler_done.set()

    timer = threading.Timer(delay_s, fire)
    timer.daemon = True
    timer.start()
    return timer


def mark_answer_started(out: AudioOut, cancel: threading.Event) -> bool:
    """Call once, before writing the FIRST answer frame: claims the output
    for the answer under the lock and, if a filler already started, waits
    for it to finish rather than interleaving. The client-side half of what
    `TtsStream._play_loop` did in the original app. Returns False if the
    turn was cancelled while waiting."""
    with out.lock:
        out.answer_started.set()
        wait_filler = out.filler_in_progress
    if wait_filler:
        while not out.filler_done.wait(0.05):
            if cancel.is_set():
                return False
    return not cancel.is_set()
