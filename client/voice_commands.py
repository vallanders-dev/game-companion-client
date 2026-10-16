"""client/voice_commands.py - "Ei, Ombri" and "chega" / "stop", heard on THIS PC.

Opt-in (settings: "Comandos de voz", default off). Nothing leaves the PC until
the name is heard: a local Vosk recognizer (Apache-2.0 small models, ~2.5% of
one core) listens in grammar mode, so it can only ever output the few listed
phrases or "[unk]". Measured offline in eval/kws_probe.py (CLAUDE.md, "Hands-
free 'Ei, Ombri'"): the English model's "omri" catches the name in both
languages; the stop words only count when said ALONE.

Modes, set by client/main.py:
- "wake"  (idle): an utterance that STARTS with "hey omri" / "omri" fires
  `wake`. If the player kept talking ("Ei Ombri, onde fica o ferreiro?") the
  utterance from the name on is kept as the question's audio, and capture
  goes on until a TAIL_SILENCE_SECONDS pause, so no second recording is
  needed; the server strips the name (companion.strip_wake_name()). A bare "Ei, Ombri." fires with no audio and the client
  records the question as after F8.
- "shush" (Ombri thinking or speaking): an utterance that IS a stop phrase
  fires `shush` - the barge-in watcher stops the answer, nothing else starts.
- None (the client is recording through its own stream): audio is ignored.

`wake` and `shush` duck-type HotkeyManager (clear()/pressed()), so they race
with F8/F6/the gamepad in the same wait loops, untouched.

Models are downloaded once into %LOCALAPPDATA%/Parca/models (sha256-pinned),
on first use - never in the installer (the feature is opt-in).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
import urllib.request
import wave
import zipfile
from pathlib import Path

RATE = 16000
BLOCK = 1600  # 0.1 s
RING_SECONDS = 30
MIN_CONF = 0.9
WAKE_PHRASES = ["hey omri", "omri"]
SHUSH_PHRASES = {
    "pt": ["chega", "silencio", "chega ombro"],
    "en": ["stop", "quiet", "enough", "shush", "stop omri"],
}
# Audio kept before the name's first word, so its start isn't clipped.
PRE_ROLL_SECONDS = 0.25
# "Ombri, como eu faço... uma espada?": Vosk ends an utterance at the first
# pause, so after the name + more speech the question is captured until the
# player has been quiet this long (Silero VAD, as the F8 recording), at most
# CAPTURE_MAX_SECONDS. Measured: cutting at Vosk's first endpoint lost
# "uma espada" (eval/voice_commands_harness.py).
TAIL_SILENCE_SECONDS = 1.0
CAPTURE_MAX_SECONDS = 20.0
VAD_THRESHOLD = 0.5

MODELS = {
    "en": ("vosk-model-small-en-us-0.15",
           "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498"),
    "pt": ("vosk-model-small-pt-0.3",
           "6e1ce909032e1afa7a88e68a3d628ecafff302bdf195befab308826c395e93b7"),
}
MODEL_URL = "https://alphacephei.com/vosk/models/{name}.zip"


def models_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Parca" / "models"


def ensure_model(lang: str, base: Path | None = None, fetch=None) -> Path:
    """The unpacked model folder, downloading and checking it first if needed.
    Raises on a failed download or a hash mismatch (nothing is unpacked then)."""
    name, sha = MODELS[lang]
    base = base or models_dir()
    target = base / name
    if (target / "am").exists() or (target / "conf").exists():
        return target
    data = (fetch or _download)(MODEL_URL.format(name=name))
    got = hashlib.sha256(data).hexdigest()
    if got != sha:
        raise ValueError(f"modelo {name}: sha256 {got[:12]}... não confere")
    base.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = base.resolve()
        for member in z.namelist():
            if not (base / member).resolve().is_relative_to(root):
                raise ValueError(f"modelo {name}: caminho inválido no zip")
        z.extractall(base)
    return target


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Ombri"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


class VoiceTrigger:
    """A voice command, duck-typing HotkeyManager for the client's wait loops."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.hotkey = name
        self._event = threading.Event()
        self._audio: bytes | None = None
        self._lock = threading.Lock()

    def fire(self, audio: bytes | None = None) -> None:
        with self._lock:
            self._audio = audio
            self._event.set()

    def clear(self) -> None:
        with self._lock:
            self._audio = None
            self._event.clear()

    def pressed(self) -> bool:
        return self._event.is_set()

    def take_audio(self) -> bytes | None:
        with self._lock:
            audio, self._audio = self._audio, None
            return audio


def to_wav(samples) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.astype("<i2").tobytes())
    return buf.getvalue()


def match(words: list[dict], mode: str, shush: list[str]) -> str | None:
    """Which command (if any) a finished utterance is: "wake" when it STARTS
    with a wake phrase, "wake+" when more speech follows the name, "shush"
    when it IS a stop phrase. Every word of the phrase needs conf >= MIN_CONF."""
    tokens = [w["word"] for w in words]
    confs = [w.get("conf", 1.0) for w in words]
    if mode == "wake":
        for phrase in WAKE_PHRASES:
            pw = phrase.split()
            if tokens[:len(pw)] == pw and min(confs[:len(pw)]) >= MIN_CONF:
                return "wake+" if len(tokens) > len(pw) else "wake"
    elif mode == "shush":
        if " ".join(tokens) in shush and min(confs) >= MIN_CONF:
            return "shush"
    return None


class VoiceListener:
    def __init__(self, language: str, base: Path | None = None) -> None:
        self.language = "en" if language == "en" else "pt"
        self.base = base
        self.wake = VoiceTrigger("voz:ei-ombri")
        self.shush = VoiceTrigger("voz:chega")
        self._mode: str | None = "wake"
        self._mode_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.error: str | None = None
        self.paused = False

    # -- mode control (client/main.py) --------------------------------------
    def set_mode(self, mode: str | None) -> None:
        with self._mode_lock:
            self._mode = mode

    @property
    def mode(self) -> str | None:
        return self._mode

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="voice-commands", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # -- the loop ------------------------------------------------------------
    def _run(self) -> None:
        try:
            import numpy as np
            import sounddevice as sd
            from vosk import KaldiRecognizer, Model, SetLogLevel
            from client.listen import VAD_MODEL_PATH, _SileroOnnx
            SetLogLevel(-1)
            vad = _SileroOnnx(VAD_MODEL_PATH)  # its own instance: the recorder's keeps state
            en = Model(str(ensure_model("en", self.base)))
            shush_model = en if self.language == "en" else Model(str(ensure_model("pt", self.base)))
        except Exception as exc:  # noqa: BLE001 - the feature just stays off
            self.error = str(exc)
            return
        shush = SHUSH_PHRASES[self.language]
        grammars = {"wake": (en, WAKE_PHRASES), "shush": (shush_model, shush)}
        ring = np.zeros(RATE * RING_SECONDS, dtype=np.int16)
        total = 0  # samples captured since start
        rec, rec_mode, rec_start = None, None, 0
        capture_from: int | None = None  # sample index while capturing a question
        quiet = 0.0
        try:
            stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK)
            stream.start()
        except Exception as exc:  # noqa: BLE001
            self.error = f"microfone: {exc}"
            return
        self.ready.set()
        with stream:
            while not self._stop.is_set():
                try:
                    block, _ = stream.read(BLOCK)
                except Exception:  # noqa: BLE001 - device gone: try again shortly
                    time.sleep(0.5)
                    continue
                mono = block[:, 0] if block.ndim > 1 else block
                n = len(mono)
                idx = (total + np.arange(n)) % len(ring)
                ring[idx] = mono
                total += n
                if capture_from is not None:
                    for i in range(0, n - 511, 512):
                        speech = vad(mono[i:i + 512].astype(np.float32) / 32768.0) >= VAD_THRESHOLD
                        quiet = 0.0 if speech else quiet + 512 / RATE
                    if quiet >= TAIL_SILENCE_SECONDS or total - capture_from >= CAPTURE_MAX_SECONDS * RATE:
                        first = max(capture_from, total - len(ring) + 1)
                        self.wake.fire(to_wav(ring[np.arange(first, total) % len(ring)]))
                        capture_from, rec_mode = None, "restart"
                    continue
                mode = None if self.paused else self._mode
                if mode != rec_mode:
                    rec, rec_mode, rec_start = None, mode, total
                    if mode in grammars:
                        model, phrases = grammars[mode]
                        rec = KaldiRecognizer(model, RATE, json.dumps(phrases + ["[unk]"]))
                        rec.SetWords(True)
                if rec is None or not rec.AcceptWaveform(mono.tobytes()):
                    continue
                words = json.loads(rec.Result()).get("result") or []
                hit = match(words, mode, shush) if words else None
                if hit == "shush":
                    self.shush.fire()
                elif hit == "wake":
                    self.wake.fire(None)  # just the name: the client records the question
                elif hit == "wake+":
                    first = rec_start - n + int(words[0]["start"] * RATE) - int(PRE_ROLL_SECONDS * RATE)
                    capture_from, quiet = max(first, total - len(ring) + 1, 0), 0.0
                    vad.reset_states()
