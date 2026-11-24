"""client/own_voice.py — the voice loop's side of the player's own keys
(docs/chat_redesign.md, 2026-11-23). No Qt.

`OwnSpeaker` speaks the answer's checked text pieces (turn_text) with the
player's own voice, one piece after another on its own thread, so the first
sentence plays while the next is being made - the same order the server's
stream had. `own_transcribe()` turns a recording into the question text with
the player's own ears.

A failure is never the player's problem: the answer falls back to Ombri's own
service and its text is still in the chat. The player is told (`on_failed`:
client/main.py shows a notice and marks the Avançado card, owner 2026-11-23),
once per reason. A key that was refused, lacks a permission or ran out of
credit stays off for this run (`FAILED` holds a fingerprint of that key, so a
new key pasted in Avançado is tried at once); no connection or the service's
rate limit only skip that turn.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
from pathlib import Path

from client import own_keys, own_speech

FAILED: dict[str, str] = {}  # category -> fingerprint of the key that failed for good in this run
LASTING = {"refused", "permission", "credit"}  # the key itself is the problem
on_failed = None  # (category, code, detail) -> None; set by client/main.py
_told: set[tuple[str, str]] = set()  # (category, code) already shown this run


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def settings_for(category: str) -> tuple[str, str, str] | None:
    """(provider, key, voice) while the player's own key is on and hasn't failed
    for good in this run, else None. Read between turns (the settings panel writes it)."""
    key = own_keys.key_for(category)
    if not key or FAILED.get(category) == _fingerprint(key):
        return None
    state = own_keys.load()[category]
    return state["provider"], key, state["voice"]


def report(category: str, key: str, exc: own_speech.OwnSpeechError) -> None:
    """A key failed: off for this run when the key is the problem, and the player is told."""
    code = getattr(exc, "code", "other")
    if code in LASTING:
        FAILED[category] = _fingerprint(key)
    print(f"  (sua chave de {'voz' if category == 'voice' else 'ouvido'} falhou: {exc} - "
          f"usando o serviço do Ombri)", flush=True)
    if (category, code) in _told or on_failed is None:
        return
    _told.add((category, code))
    try:
        on_failed(category, code, getattr(exc, "detail", ""))
    except Exception:  # noqa: BLE001 - telling the player never breaks the turn
        pass


def own_transcribe(own: tuple[str, str, str], wav: bytes, language: str) -> str | None:
    """The question's words, or None when the player's ears failed (use Ombri's)."""
    provider, key, _voice = own
    try:
        return own_speech.transcribe(provider, key, wav, language)
    except own_speech.OwnSpeechError as exc:
        report("ears", key, exc)
        return None


# -- the fixed lines ("Anotado.", fillers, the cap, the goodbye...) ----------------------
# Ombri plays these from baked WAVs in its own voice. With the player's own voice
# they are made once with the player's key (a few hundred characters per voice),
# kept on the PC and played from there, so one session doesn't mix two voices
# (owner, 2026-11-23). Never made in the middle of a turn: `cached_line` only
# reads the cache; `prewarm` fills it on its own thread between turns, and until
# a line is there the baked one plays.
def cache_dir() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home()) / "Parca" / "own_voice_lines"


def _line_texts(ombri_voice: str) -> dict[str, str]:
    """name -> text of every baked line of that Ombri voice (its manifest, in its language)."""
    from client.fixed_lines import MANIFEST_NAME, voice_dir
    try:
        data = json.loads((voice_dir(ombri_voice) / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v.strip()}


def _line_file(own: tuple[str, str, str], text: str, root: Path) -> Path:
    provider, _key, voice = own
    digest = hashlib.sha256(f"{provider}|{voice}|{own_speech.ELEVEN_MODEL}|{text}".encode("utf-8")).hexdigest()[:24]
    return root / f"{digest}.pcm"


def cached_line(name: str, ombri_voice: str, root: Path | None = None) -> bytes | None:
    """The line in the player's own voice when it's on and already made, else None
    (the caller plays the baked one). Reads files only: never a network call."""
    own = settings_for("voice")
    if own is None or not own[2]:
        return None
    text = _line_texts(ombri_voice).get(name)
    if not text:
        return None
    try:
        return _line_file(own, text, root or cache_dir()).read_bytes() or None
    except OSError:
        return None


_warming: set[tuple] = set()
_ready: dict[tuple, int] = {}  # (provider, voice, ombri voice) -> lines made so far (fillers reload on change)


def lines_signature(ombri_voice: str) -> tuple | None:
    own = settings_for("voice")
    if own is None or not own[2]:
        return None
    sig = (own[0], own[2], ombri_voice)
    return (*sig, _ready.get(sig, 0))


def prewarm(ombri_voice: str, root: Path | None = None, synthesize=None, wait: bool = False) -> None:
    """Makes the missing fixed lines with the player's own voice, on its own thread
    (once per voice per run). Stops at the first failure: `report` tells the player."""
    own = settings_for("voice")
    if own is None or not own[2]:
        return
    sig = (own[0], own[2], ombri_voice)
    if sig in _warming:
        return
    _warming.add(sig)
    root = root or cache_dir()
    synthesize = synthesize or own_speech.synthesize

    def work() -> None:
        made = 0
        for name, text in _line_texts(ombri_voice).items():
            if name == "sample":  # the settings preview of Ombri's own voices, never played as the player's
                continue
            path = _line_file(own, text, root)
            if path.exists():
                made += 1
                continue
            try:
                pcm = synthesize(own[0], own[1], own[2], text)
            except own_speech.OwnSpeechError as exc:
                report("voice", own[1], exc)
                _warming.discard(sig)  # a new key, or the next run, tries again
                break
            if not pcm:
                continue
            try:
                root.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(path.name + ".tmp")
                tmp.write_bytes(pcm)
                os.replace(tmp, path)
                made += 1
            except OSError:
                break
        _ready[sig] = made

    th = threading.Thread(target=work, name="own-voice-lines", daemon=True)
    th.start()
    if wait:
        th.join()


class OwnSpeaker:
    def __init__(self, own: tuple[str, str, str], audio_out, cancel: threading.Event, on_first=None,
                 synthesize=None) -> None:
        self.provider, self._key, self.voice = own
        self.audio_out = audio_out
        self.cancel = cancel
        self.on_first = on_first  # called before the first frame (fillers give way); False = stop
        self.failed = False
        self.spoke = False
        self._synthesize = synthesize  # None: own_speech.synthesize, streamed (the first sound comes sooner)
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="own-voice", daemon=True)
        self._thread.start()

    def say(self, text: str) -> None:
        if text and text.strip():
            self._queue.put(text)

    def wait_spoken(self, timeout: float = 60.0) -> None:
        """Waits until what was said so far has played (the F6 "save it?" before the mic opens)."""
        done = threading.Event()
        self._queue.put(done)  # type: ignore[arg-type]
        done.wait(timeout)

    def finish(self, timeout: float = 120.0) -> None:
        """Waits until every piece was spoken (or the turn was cancelled)."""
        self._queue.put(None)
        self._thread.join(timeout)

    def _run(self) -> None:
        while True:
            text = self._queue.get()
            if text is None:
                return
            if isinstance(text, threading.Event):
                text.set()
                continue
            if self.failed or self.cancel.is_set():
                continue
            try:
                if self._synthesize is None:
                    own_speech.synthesize(self.provider, self._key, self.voice, text, on_chunk=self._play)
                    continue
                pcm = self._synthesize(self.provider, self._key, self.voice, text)
            except own_speech.OwnSpeechError as exc:
                self.failed = True
                report("voice", self._key, exc)
                continue
            self._play(pcm)

    def _play(self, pcm: bytes) -> bool:
        """One piece of audio; False stops the stream (cancelled, or the fillers kept the turn)."""
        if not pcm or self.cancel.is_set():
            return not self.cancel.is_set()
        if not self.spoke:
            self.spoke = True
            if self.on_first is not None and self.on_first() is False:
                self._stopped = True
        if getattr(self, "_stopped", False):
            return False
        return self.audio_out.write(pcm, self.cancel) is not False
