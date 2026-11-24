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
  needed; the server strips the name (companion.strip_wake_name()). A bare "Ei, Ombri." waits
  NAME_PAUSE_SECONDS: speech in that time joins the question ("Ombri... acorda"); otherwise
  it fires with no audio and the client records the question as after F8.
- "shush" (Ombri thinking or speaking): an utterance that IS a stop phrase
  fires `shush` - the barge-in watcher stops the answer, nothing else starts.
- None (the client is recording through its own stream): audio is ignored.

The Build pane's words (server/build_guide.py; build guide PR 4, 2026-11-23):
while a guide is open (`set_guide(True)`) and the mode is "wake", a second
recognizer on the language's own model hears GUIDE_PHRASES - "próxima" /
"volta" / "repete" (en "next" / "back" / "repeat", es "siguiente" / "atrás" /
"repite") - and fires `guide` with the command when one is said ALONE at
MIN_CONF, the stop-words rule: instant, free, on the PC. "Próxima camada",
"volta uma", "quanto falta de tábua?" and everything else still need the name
or F8 and go through the server's matcher ("[unk] próxima" never fires here).
ALONE is checked twice: the utterance is that one word, AND the VAD hears no
speech around it (said_alone) - the grammar decoder decodes the rest of a
sentence as silence, so "próxima camada" alone would have fired. The words
were checked against the three pinned models' vocabularies (their Gr.fst
symbol tables, 2026-11-23): the pt model has only the accented "próxima", so
that is the form listed, and the grammar goes to Vosk as raw UTF-8
(grammar_json) - it does not decode json's \\u escapes.

`wake`, `shush` and `guide` duck-type HotkeyManager (clear()/pressed()), so
they race with F8/F6/the gamepad in the same wait loops, untouched.

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
WAKE_PHRASES = ["hey omri", "omri", "wake up omri"]
# "Wake up, Ombri" (Tony, 2026-11-23): the name comes LAST, so the whole
# utterance is the command - kept as audio, never a bare name that waits.
WHOLE_COMMANDS = {"wake up omri"}
SHUSH_PHRASES = {
    "pt": ["chega", "silencio", "chega ombro"],
    "en": ["stop", "quiet", "enough", "shush", "stop omri"],
    # Spanish (2026-11-03, docs/spanish.md §4.C). Heard with the es model
    # since its hash was pinned (2026-11-04); both words are in its
    # vocabulary (and the pt model's, the fallback). "ya basta" / "cállate"
    # can come later, measured in eval/kws_probe.py as the others were.
    "es": ["basta", "silencio"],
}
# The Build pane's local words, per MODEL language (the model that hears the
# stop words hears these too): what is said -> the pane command. Each must be
# in that model's vocabulary, else Vosk drops it from the grammar in silence
# ("Ignoring word missing in vocabulary"); checked 2026-11-23 in the three
# pinned models' Gr.fst symbol tables - pt lacks the unaccented "proxima",
# es has both "atrás" and "atras". One word each, said alone: a longer
# utterance comes out as "[unk]" around it and never fires.
GUIDE_PHRASES = {
    "pt": {"próxima": "next", "volta": "back", "repete": "repeat"},
    "en": {"next": "next", "back": "back", "repeat": "repeat"},
    "es": {"siguiente": "next", "atrás": "back", "repite": "repeat"},
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
# "Ombri, acorda": Vosk ends the utterance at the comma's pause. Firing there lost
# "acorda", spoken before the client's recording had opened (owner's retakes,
# 2026-11-04). So a bare name waits this long for more speech before it fires.
NAME_PAUSE_SECONDS = 0.8
# The pane's words count only when said ALONE: no speech (Silero VAD) this
# long before the word nor after it. Vosk's small models, given a three-word
# grammar, decode the REST of a sentence as silence - "próxima camada" came
# out as "próxima" alone at conf 1.00, and ordinary Spanish talk popped
# "atrás" / "repite" seven times a minute (eval/guide_words_probe.py,
# 2026-11-23). The recognizer's end-point already means >= 0.5 s of
# (decoded) quiet after the word; the VAD is asked whether it really was.
GUIDE_GAP_SECONDS = 0.5
# The word's own edges, left out of the windows: Vosk's times are a little
# off, the END more so - the grammar decoder gave "repete" as 1.56-2.04 s
# with the VAD hearing it until 2.27 s (its last syllable taken for silence).
GUIDE_EDGE_SECONDS = (0.15, 0.3)  # (before the start, after the end)

MODELS = {
    "en": ("vosk-model-small-en-us-0.15",
           "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498"),
    "pt": ("vosk-model-small-pt-0.3",
           "6e1ce909032e1afa7a88e68a3d628ecafff302bdf195befab308826c395e93b7"),
    # Pinned 2026-11-04 from alphacephei.com (39,817,833 bytes). An empty
    # hash makes ensure_model() refuse and the stop words fall back to pt.
    "es": ("vosk-model-small-es-0.42",
           "09b239888f633ef2f0b4e09736e3d9936acfd810bc65d53fad45261762c6511f"),
}
MODEL_URL = "https://alphacephei.com/vosk/models/{name}.zip"


def models_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Parca" / "models"


def listener_language(language: str) -> str:
    """The VoiceListener's language for a voice's language: one with stop
    words of its own, else Portuguese (the original)."""
    return language if language in SHUSH_PHRASES else "pt"


def shush_model_language(language: str) -> str:
    """Which model hears the stop words: the language's own when its hash is
    pinned (MODELS), else the Portuguese one."""
    name, sha = MODELS.get(language, ("", ""))
    return language if sha else "pt"


def ensure_model(lang: str, base: Path | None = None, fetch=None) -> Path:
    """The unpacked model folder, downloading and checking it first if needed.
    Raises on a failed download or a hash mismatch (nothing is unpacked then),
    and on a model whose hash isn't pinned yet (never trusted on faith)."""
    name, sha = MODELS[lang]
    if not sha:
        raise ValueError(f"modelo {name}: sem sha256 pinado em MODELS")
    base = base or models_dir()
    target = base / name
    if model_present(target):
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


def model_present(target: Path) -> bool:
    """An unpacked model: the acoustic model file in either of Vosk's layouts
    - `am/final.mdl` (en-us-0.15, es-0.42) or `final.mdl` at the root (the
    older flat layout of pt-0.3). Until 2026-11-23 only `am/` or `conf/` was
    looked for, so the Portuguese model was downloaded again (31 MB) at every
    listener start."""
    return (target / "am" / "final.mdl").exists() or (target / "final.mdl").exists()


def guide_words(model_language: str) -> dict[str, str]:
    """The pane's words for the model that hears the stop words (pt when the
    language's own model isn't pinned, like the stop words themselves)."""
    return GUIDE_PHRASES.get(model_language) or GUIDE_PHRASES["pt"]


def guide_word(language: str, command: str) -> str:
    """What a player says for a pane command in a voice's language ("repete"
    for pt "repeat"): the console line, and the typed question main.py sends
    for "repeat" (in the voice's language, the one the server matches)."""
    words = GUIDE_PHRASES.get(listener_language(language)) or GUIDE_PHRASES["pt"]
    return next((w for w, c in words.items() if c == command), command)


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
        self._command: str | None = None  # the guide trigger: "next" / "back" / "repeat"
        self._lock = threading.Lock()

    def fire(self, audio: bytes | None = None, command: str | None = None) -> None:
        with self._lock:
            self._audio = audio
            self._command = command
            self._event.set()

    def clear(self) -> None:
        with self._lock:
            self._audio = None
            self._command = None
            self._event.clear()

    def pressed(self) -> bool:
        return self._event.is_set()

    def take_audio(self) -> bytes | None:
        with self._lock:
            audio, self._audio = self._audio, None
            return audio

    def take_command(self) -> str | None:
        with self._lock:
            command, self._command = self._command, None
            return command


def grammar_json(phrases: list[str]) -> str:
    """The grammar KaldiRecognizer takes: the phrases plus "[unk]", as raw
    UTF-8. json.dumps' default `\\u00f3` escapes are NOT decoded by Vosk's
    grammar parser - it logged "Ignoring word missing in vocabulary:
    'pr\\u00f3xima'" and dropped the word (eval/guide_words_probe.py,
    2026-11-23); the name and the stop words are plain ASCII, so they never
    met this."""
    return json.dumps(list(phrases) + ["[unk]"], ensure_ascii=False)


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
                return "wake+" if len(tokens) > len(pw) or phrase in WHOLE_COMMANDS else "wake"
    elif mode == "shush":
        if " ".join(tokens) in shush and min(confs) >= MIN_CONF:
            return "shush"
    return None


def match_guide(words: list[dict], phrases: dict[str, str]) -> str | None:
    """The pane command ("next" / "back" / "repeat") when the finished
    utterance IS one guide word (GUIDE_PHRASES) at conf >= MIN_CONF - the
    stop-words rule. "[unk] próxima", "próxima [unk]" and a low-confidence
    word are None: those go through the name or F8 and the server."""
    if len(words) != 1:
        return None
    word = words[0]
    if word.get("conf", 1.0) < MIN_CONF:
        return None
    return phrases.get(word.get("word", ""))


def said_alone(ring, total: int, first: int, word: dict, vad) -> bool:
    """Was the guide word spoken on its own? True when the VAD hears no
    speech in the GUIDE_GAP_SECONDS before it nor in the audio after it (up
    to now - at least the recognizer's own end-point quiet). `ring` is the
    listener's circular int16 buffer holding samples up to `total`; `first`
    is the index of the first sample the guide recognizer heard, which its
    word times count from. The VAD's state is reset around the check: it
    is the listener's own instance (not the recorder's)."""
    import numpy as np

    start = first + int(word.get("start", 0.0) * RATE)
    end = first + int(word.get("end", 0.0) * RATE)
    lead, trail = (int(e * RATE) for e in GUIDE_EDGE_SECONDS)
    gap = int(GUIDE_GAP_SECONDS * RATE)
    oldest = max(total - len(ring) + 1, 0)
    windows = ((max(start - gap, oldest), max(start - lead, oldest)), (min(end + trail, total), total))
    vad.reset_states()
    try:
        for lo, hi in windows:
            for i in range(lo, hi - 511, 512):
                frame = ring[np.arange(i, i + 512) % len(ring)].astype(np.float32) / 32768.0
                if vad(frame) >= VAD_THRESHOLD:
                    return False
        return True
    finally:
        vad.reset_states()


class VoiceListener:
    def __init__(self, language: str, base: Path | None = None) -> None:
        self.language = listener_language(language)
        self.base = base
        self.wake = VoiceTrigger("voz:ei-ombri")
        self.shush = VoiceTrigger("voz:chega")
        self.guide = VoiceTrigger("voz:guia")  # the Build pane's own words, while a guide is open
        self._mode: str | None = "wake"
        self._guide_open = False
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

    def set_guide(self, open_: bool) -> None:
        """Whether a build guide is open in the pane (client/main.py keeps it
        in step with its `_GUIDE` mirror): the guide words are heard only
        then, and only in "wake" mode."""
        with self._mode_lock:
            self._guide_open = bool(open_)

    @property
    def guide_open(self) -> bool:
        return self._guide_open

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
            shush_lang = shush_model_language(self.language)
            shush_model = en if shush_lang == "en" else Model(str(ensure_model(shush_lang, self.base)))
        except Exception as exc:  # noqa: BLE001 - the feature just stays off
            self.error = str(exc)
            return
        shush = SHUSH_PHRASES[self.language]
        grammars = {"wake": (en, WAKE_PHRASES), "shush": (shush_model, shush)}
        # The pane's words: the language's own model (the stop words' one),
        # fed the same blocks as the wake recognizer while a guide is open.
        guide = guide_words(shush_lang)
        ring = np.zeros(RATE * RING_SECONDS, dtype=np.int16)
        total = 0  # samples captured since start
        rec, rec_mode, rec_start = None, None, 0
        grec, grec_first = None, 0  # the guide recognizer, only in "wake" mode with a guide open
        capture_from: int | None = None  # sample index while capturing a question
        quiet = 0.0
        name_only = False  # capturing, and nothing has followed the name yet
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
                        name_only = name_only and not speech
                    if name_only:
                        if quiet >= NAME_PAUSE_SECONDS:
                            self.wake.fire(None)  # just the name: the client records the question
                            capture_from, rec_mode, name_only = None, "restart", False
                    elif quiet >= TAIL_SILENCE_SECONDS or total - capture_from >= CAPTURE_MAX_SECONDS * RATE:
                        first = max(capture_from, total - len(ring) + 1)
                        self.wake.fire(to_wav(ring[np.arange(first, total) % len(ring)]))
                        capture_from, rec_mode = None, "restart"
                    continue
                mode = None if self.paused else self._mode
                if mode != rec_mode:
                    rec, rec_mode, rec_start = None, mode, total
                    grec = None  # its audio continuity broke too: built again below
                    if mode in grammars:
                        model, phrases = grammars[mode]
                        rec = KaldiRecognizer(model, RATE, grammar_json(phrases))
                        rec.SetWords(True)
                guide_on = mode == "wake" and self._guide_open
                if guide_on != (grec is not None):
                    grec = None
                    if guide_on:
                        grec = KaldiRecognizer(shush_model, RATE, grammar_json(list(guide)))
                        grec.SetWords(True)
                        grec_first = total - n  # this block is the first it hears
                data = mono.tobytes()
                if grec is not None and grec.AcceptWaveform(data):
                    gwords = json.loads(grec.Result()).get("result") or []
                    command = match_guide(gwords, guide) if gwords else None
                    if command and said_alone(ring, total, grec_first, gwords[0], vad):
                        self.guide.fire(command=command)
                if rec is None or not rec.AcceptWaveform(data):
                    continue
                words = json.loads(rec.Result()).get("result") or []
                hit = match(words, mode, shush) if words else None
                if hit == "shush":
                    self.shush.fire()
                elif hit in ("wake", "wake+"):
                    name_only = hit == "wake"
                    first = rec_start - n + int(words[0]["start"] * RATE) - int(PRE_ROLL_SECONDS * RATE)
                    capture_from, quiet = max(first, total - len(ring) + 1, 0), 0.0
                    vad.reset_states()
