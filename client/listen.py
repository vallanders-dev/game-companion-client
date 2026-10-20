"""Microphone capture with automatic silence-based stop.

After the capture hotkey fires, the player speaks their question and this records
it with no second keypress to end - it stops once the mic goes quiet, with a hard
max-duration cap as a safety net.

Speech/silence classification uses Silero VAD (a small neural voice-activity
model), not amplitude thresholding - see README "Why Silero VAD replaced RMS
thresholding" for the reasoning and the investigation that led here.

Output: 16 kHz mono PCM16 WAV bytes (written with the stdlib ``wave`` module),
which is what ElevenLabs Scribe expects. Needs ``sounddevice``, ``numpy`` and
``onnxruntime`` only: the Silero model ships as ``assets/silero_vad.onnx``
(MIT, see ``assets/SILERO_VAD_LICENSE.txt``) and runs through ``_SileroOnnx``
below - NOT the ``silero-vad`` package, whose ONNX wrapper needs ``torch``
(a ~500 MB install) purely as a tensor container around one onnxruntime call.
"""
from __future__ import annotations

import io
import wave
from pathlib import Path

VAD_MODEL_PATH = Path(__file__).resolve().parent / "assets" / "silero_vad.onnx"
# silero-vad 6.2.1's bundled silero_vad.onnx. Pinned so a swapped file is
# caught at load time instead of silently changing speech detection.
VAD_MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"

SAMPLE_RATE = 16_000       # Scribe's working rate; also Silero VAD's native rate
_CHANNELS = 1
# Silero VAD requires exactly 512-sample chunks at 16 kHz (256 at 8 kHz) - not
# a tunable choice, a hard model requirement. 512/16000 = 32 ms, close to the
# RMS approach's old 30 ms blocks.
_VAD_WINDOW_SAMPLES = 512
_BLOCK_SECONDS = _VAD_WINDOW_SAMPLES / SAMPLE_RATE

# Audible cues - the console print alone is invisible during real play (the
# terminal is meant to be minimized/moved away while gaming), which directly
# caused a no-speech timeout in testing: the player had no way to know the
# mic had actually opened. Generated locally with numpy and played via
# sounddevice (already loaded for recording) rather than through
# speech.play()/ElevenLabs - zero network round trip, zero decode step, so a
# cue can fire at the exact instant it's needed. See README - "Audible cues".
_TONE_SAMPLE_RATE = 44_100
_TONE_DURATION = 0.15      # seconds, for the plain single-tone start/stop cues
_TONE_FADE = 0.01          # seconds of fade in/out, avoids an audible click
_TONE_VOLUME = 0.35
_START_TONE_HZ = 880.0     # rising cue: "go ahead, talk"
_STOP_TONE_HZ = 440.0      # one octave down: "heard you, stop talking"
# "Note saved" (F6 remember-flow) is a two-note ascending chime, not a third
# flat pitch: a different pitch alone is easy to mishear as one of the other
# two, but a two-note chime is structurally unmistakable even by ear alone.
_CONFIRM_NOTES = ((659.25, 0.09), (987.77, 0.13))  # E5, then B5
_CONFIRM_GAP = 0.02  # seconds of silence between the two notes


def _tone_wave(frequency: float, duration: float):
    """Build one faded sine-wave tone as float32 samples. Never plays it."""
    import numpy as np

    n = int(_TONE_SAMPLE_RATE * duration)
    t = np.linspace(0, duration, n, endpoint=False)
    tone = np.sin(2 * np.pi * frequency * t)
    fade = max(1, int(_TONE_SAMPLE_RATE * min(_TONE_FADE, duration / 2)))
    envelope = np.ones(n)
    envelope[:fade] = np.linspace(0.0, 1.0, fade)
    envelope[-fade:] = np.linspace(1.0, 0.0, fade)
    return (tone * envelope * _TONE_VOLUME).astype(np.float32)


def _play_wave(samples) -> None:
    """Best-effort playback of pre-built samples. Never raises, never blocks
    the caller - a missing or failed cue must never interrupt recording."""
    try:
        import sounddevice as sd

        sd.play(samples, _TONE_SAMPLE_RATE)
    except Exception:  # noqa: BLE001
        pass


def _play_tone(frequency: float) -> None:
    """Best-effort short local beep - the plain start/stop cues."""
    try:
        _play_wave(_tone_wave(frequency, _TONE_DURATION))
    except Exception:  # noqa: BLE001
        pass


def play_stop_tone() -> None:
    """Public wrapper for the 'stop' cue, for callers outside the recording
    loop that need a free, local, audible signal - main.py uses it when the
    daily API cap blocks a spoken reply and no cached MP3 exists."""
    _play_tone(_STOP_TONE_HZ)


def play_confirm_tone() -> None:
    """Third audible cue: a personal note was successfully saved. Called from
    main.py's remember_note() after the Chroma upsert + JSON write both
    succeed - not from this module's own recording loop, since the save
    happens well after recording ends."""
    try:
        import numpy as np

        notes = [_tone_wave(freq, dur) for freq, dur in _CONFIRM_NOTES]
        gap = np.zeros(int(_TONE_SAMPLE_RATE * _CONFIRM_GAP), dtype=np.float32)
        _play_wave(np.concatenate([notes[0], gap, notes[1]]))
    except Exception:  # noqa: BLE001
        pass


def _load_backend():
    import numpy
    import sounddevice

    return sounddevice, numpy


# Loaded once per process, not once per recording - Silero VAD is a real
# (small) neural model, and reloading it on every hotkey press would add a
# multi-second delay to every single turn. `preload_vad_model()` warms this
# during app startup (see main.py) so the very first recording isn't the one
# that pays for it; a call to `record_until_silence()` before that still
# works, just loads lazily on that first call instead.
_vad_model = None


class _SileroOnnx:
    """Numpy port of silero-vad 6.2.1's ``OnnxWrapper`` (utils_vad.py), 16 kHz
    and batch 1 only - the only way this module calls it. Same model file,
    same onnxruntime session options (1 intra-op + 1 inter-op thread), same
    inputs: ``input`` = the previous call's last 64 samples + this 512-sample
    window, ``state`` = [2, 1, 128] carried between calls, ``sr`` = 16000.
    Verified equal to the torch-backed wrapper window for window
    (``eval/vad_equivalence_harness.py``)."""

    _CONTEXT = 64

    def __init__(self, path: Path) -> None:
        import hashlib

        import numpy as np
        import onnxruntime

        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != VAD_MODEL_SHA256:
            raise RuntimeError(f"{path.name} inesperado (sha256 {digest[:12]}...)")
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self._np = np
        self._session = onnxruntime.InferenceSession(data, sess_options=opts,
                                                     providers=["CPUExecutionProvider"])
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset_states()

    def reset_states(self) -> None:
        np = self._np
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, self._CONTEXT), dtype=np.float32)

    def __call__(self, window) -> float:
        np = self._np
        x = np.concatenate([self._context, window.reshape(1, -1).astype(np.float32, copy=False)], axis=1)
        out, self._state = self._session.run(None, {"input": x, "state": self._state, "sr": self._sr})
        self._context = x[:, -self._CONTEXT:]
        return float(out[0, 0])


def _load_vad_model():
    global _vad_model
    if _vad_model is None:
        # The ONNX build, not torch.jit (which warns on Python 3.14) - the
        # original backend switch was cross-checked on real speech, silence
        # and noise (agreement within 2e-6). See README - "Why Silero VAD
        # replaced RMS thresholding".
        _vad_model = _SileroOnnx(VAD_MODEL_PATH)
    return _vad_model


def preload_vad_model() -> bool:
    """Best-effort warm-up so the model is already loaded before the first
    recording. Returns whether it succeeded; never raises - a failure here
    just means the first real recording pays the load cost (or fails there
    instead, with its own error message)."""
    try:
        _load_vad_model()
        return True
    except Exception:  # noqa: BLE001
        return False


# End-of-turn checks (client/turn_detect.py): seconds of silence at which the
# take so far is offered to the model; the fixed `silence_hang` (2.5 s) still
# ends a take the model never calls finished. Measured on the owner's real
# questions (Witcher session, real pauses from Scribe's word timings): asked at
# 0.3 s the model stopped 19 of 45 mid-sentence pauses early ("No, <pause> there's
# a gate..."); at 0.7 s with takes >= 1 s, 2 of 9, and 15 of 27 questions still end
# early. Shorter takes are never cut: a lone "No," / "Exactly." is usually an opener.
SMART_TURN_CHECKS = (0.7, 1.2)
SMART_TURN_MIN_TAKE = 1.0  # seconds of audio before the model is asked at all
SMART_TURN_THRESHOLD = 0.85  # = turn_detect.THRESHOLD, see there
LAST_STOP_SECONDS: float | None = None  # elapsed audio when the last take stopped (tests/telemetry)

# With live words (0.1.19, server/live_stt.py) - owner, 2026-10-20: "Ombri's wait
# ... slightly shorter, about 30%", and "Ombri cut my voice while I was using
# 'ugh... uhmm' sounds - it should keep listening". Every wait is 30% shorter,
# and a take whose last heard word leaves the thought open ("uh", "um", "you
# know", "and", "the", "tipo", "é", "de"...) is never stopped by the model and
# gets HOLD_SILENCE instead. No live words = the timings above, unchanged.
LIVE_SMART_TURN_CHECKS = (0.5, 0.85)
LIVE_SILENCE_FACTOR = 0.7
HOLD_SILENCE = 3.0
_OPEN_ENDINGS = {
    "en": ("uh", "um", "uhm", "umm", "uhh", "er", "erm", "hmm", "hm", "ah", "so", "and", "but", "or", "the", "a",
           "an", "of", "to", "with", "like", "because", "cause", "if", "then", "my", "your", "his", "her", "is",
           "are", "was", "in", "on", "at", "for", "from", "that", "which", "where", "how", "what", "who", "when",
           "you know", "i mean", "kind of", "sort of", "let me", "let me see", "wait", "about", "this", "these",
           "i", "i m", "it s", "there s", "we", "they", "can", "could", "should", "would", "do", "does", "did"),
    "pt": ("e", "eh", "ah", "ahn", "hum", "hm", "hmm", "um", "uma", "uh", "tipo", "entao", "mas", "ou", "o", "a",
           "os", "as", "de", "do", "da", "dos", "das", "no", "na", "nos", "nas", "pra", "para", "pro", "com",
           "que", "porque", "se", "meu", "minha", "seu", "sua", "ai", "ne", "assim", "sabe", "tipo assim",
           "deixa eu", "deixa eu ver", "pera", "perai", "espera", "como", "onde", "quando", "qual", "quem",
           "esse", "essa", "isso", "eu", "ele", "ela", "tem", "ta", "to", "e o", "e a", "em", "por", "pelo",
           "pela", "sobre", "ate"),
}


def ends_mid_thought(text: str, language: str = "pt") -> bool:
    """True when the live words so far stop on a filler or a connective - the
    player is still in the middle of saying something. A final "?" means done."""
    raw = (text or "").strip()
    if not raw:
        return False
    if raw.endswith(("-", "\u2014", "\u2013", "...", "\u2026", ",")):
        return True
    if raw.endswith(("?", "!")):
        return False
    import unicodedata
    folded = "".join(c for c in unicodedata.normalize("NFKD", raw.lower()) if not unicodedata.combining(c))
    words = "".join(c if c.isalnum() else " " for c in folded).split()
    if not words:
        return False
    endings = _OPEN_ENDINGS.get(language, ()) + (_OPEN_ENDINGS["pt"] if language != "pt" else ())
    tail1, tail2, tail3 = words[-1], " ".join(words[-2:]), " ".join(words[-3:])
    return tail1 in endings or tail2 in endings or tail3 in endings


def record_until_silence(
    *,
    silence_hang: float = 2.5,
    max_duration: float = 25.0,
    min_duration: float = 0.5,
    vad_threshold: float = 0.5,
    speech_pad_ms: float = 30.0,
    no_speech_timeout: float = 4.0,
    start_tone: bool = True,
    abort=None,
    min_speech_onset: float = 0.0,
    smart_turn: bool = True,
    live=None,
) -> bytes | None:
    """Record the mic until it goes quiet, then return WAV bytes.

    The follow-up window (client/main.py) records with ``start_tone=False``
    (no beep after every answer), ``abort`` (a callable polled every block;
    True ends the recording at once with ``None`` - a key was pressed) and
    ``min_speech_onset`` (seconds of speech blocks needed before the take
    counts as speech, so a cough or a game sound doesn't open a paid turn).

    Each ~32 ms block is classified by Silero VAD as speech or silence
    (``vad_threshold`` on its speech-probability output, default matches
    Silero's own recommended default) rather than by comparing amplitude
    against a calibrated noise floor. Stops once classified-silence persists
    for ``silence_hang`` seconds - but not before ``min_duration`` seconds.
    ``max_duration`` is a hard cap for when silence detection never triggers
    (mic glitch, constant background noise).

    ``speech_pad_ms`` pads the end of the kept audio past the last block
    Silero classified as speech, so a fading trailing word isn't clipped
    right at the classification boundary - this is the one and only
    tail-padding mechanism (there used to be a separate fixed-buffer trim
    here too; see README "Why Silero VAD replaced RMS thresholding" for why
    that was retired rather than kept alongside this).

    ``smart_turn`` (0.1.18): at the short pauses in SMART_TURN_CHECKS the take
    so far goes to the end-of-turn model (client/turn_detect.py); "finished"
    stops the recording right there instead of after ``silence_hang``. No model
    (not downloaded yet, failed) = the fixed wait, exactly as before.

    ``live`` (0.1.19): an object with ``feed(block, speaking)`` (each block is
    streamed to the server for live transcription), ``ok`` and
    ``open_ended()`` (the words so far end mid-thought - ends_mid_thought()).
    While it works the waits are LIVE_* (30% shorter) and an open-ended take
    is held up to HOLD_SILENCE instead of being stopped.

    Returns ``None`` (caller should fall back to typed input) when there is no
    usable input device, the stream errors, Silero VAD can't be loaded, or
    nothing is spoken within ``no_speech_timeout`` seconds.
    """
    try:
        sd, np = _load_backend()
    except Exception as exc:  # noqa: BLE001 - ImportError / backend init failure
        print(f"  (áudio indisponível: {exc})")
        return None

    try:
        vad_model = _load_vad_model()
        vad_model.reset_states()  # clear state left over from any previous recording
    except Exception as exc:  # noqa: BLE001 - ImportError / model load failure
        print(f"  (detector de voz indisponível: {exc})")
        return None

    blocksize = _VAD_WINDOW_SAMPLES

    try:
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=_CHANNELS,
            dtype="float32",
            blocksize=blocksize,
        )
    except Exception as exc:  # noqa: BLE001 - PortAudioError, no device, ...
        print(f"  (não consegui abrir o microfone: {exc})")
        return None

    blocks: list = []
    silence_run = 0.0
    elapsed = 0.0
    elapsed_samples = 0
    last_speech_end_sample = 0
    speech_detected = False
    stop_reason = "erro"
    turn_model = None
    if smart_turn:
        from client import turn_detect
        turn_model = turn_detect.get_model()
    next_check = 0  # index into SMART_TURN_CHECKS; back to 0 whenever speech resumes

    try:
        with stream:
            # The instant the mic is actually live - not by the caller before
            # this call, because opening the audio device is what the real
            # latency is. Uncertainty about *exactly* when recording starts
            # caused two separate real problems in two sessions (a repeated
            # phrase, then a no-speech timeout) since the console print alone
            # is invisible while the terminal is minimized during real play -
            # see README "Audible cues". The tone is non-blocking (plays in
            # the background) so it never delays the recording loop below.
            # Same for both the ask-hotkey and remember-hotkey, since both
            # call this one function.
            if start_tone:
                _play_tone(_START_TONE_HZ)
                print("  🎙️ Escutando...")
            speech_time = 0.0
            hold_reason = ""
            while True:
                try:
                    data, _overflow = stream.read(blocksize)
                except Exception as exc:  # noqa: BLE001
                    print(f"  (erro lendo o microfone: {exc})")
                    break

                mono = data[:, 0] if data.ndim > 1 else data
                mono = mono.copy()
                blocks.append(mono)
                elapsed += _BLOCK_SECONDS
                elapsed_samples += len(mono)

                if abort is not None and abort():
                    return None

                prob = vad_model(mono)

                if prob >= vad_threshold:
                    speech_time += _BLOCK_SECONDS
                    speech_detected = speech_detected or speech_time >= min_speech_onset
                    silence_run = 0.0
                    last_speech_end_sample = elapsed_samples
                    next_check = 0
                else:
                    silence_run += _BLOCK_SECONDS

                live_on = False
                if live is not None:
                    try:
                        live.feed(mono, speech_detected)
                        live_on = bool(live.ok)
                    except Exception:  # noqa: BLE001 - live words are a bonus, never the recording
                        live = None
                checks = LIVE_SMART_TURN_CHECKS if live_on else SMART_TURN_CHECKS
                hang = silence_hang * LIVE_SILENCE_FACTOR if live_on else silence_hang
                held = False
                if live_on and silence_run >= checks[0]:
                    try:
                        held = bool(live.open_ended())
                    except Exception:  # noqa: BLE001
                        held = False
                    if held:
                        hang = max(hang, HOLD_SILENCE)
                        hold_reason = " (segurou: frase em aberto)"

                # Not while speech is still building toward the onset: a
                # sentence started just before the timeout isn't cut off.
                if not speech_detected and elapsed >= no_speech_timeout and (
                        speech_time == 0.0 or silence_run >= 0.5):
                    if start_tone:
                        print(f"  (nada falado em {elapsed:.1f}s)")
                    return None
                if (turn_model is not None and speech_detected and not held
                        and elapsed >= max(min_duration, SMART_TURN_MIN_TAKE + silence_run)
                        and next_check < len(checks)
                        and silence_run >= checks[next_check]):
                    next_check += 1
                    try:
                        p_done = turn_model.probability(np.concatenate(blocks))
                    except Exception:  # noqa: BLE001 - a model error = the fixed wait
                        p_done, turn_model = 0.0, None
                    if p_done >= SMART_TURN_THRESHOLD:
                        stop_reason = f"fim de fala ({silence_run:.1f}s, p={p_done:.2f}){hold_reason}"
                        break
                if (
                    speech_detected
                    and elapsed >= min_duration
                    and silence_run >= hang
                ):
                    stop_reason = f"{hang:.1f}s de silêncio{hold_reason}"
                    break
                if elapsed >= max_duration:
                    stop_reason = f"limite de {max_duration:.0f}s atingido"
                    break
    except Exception as exc:  # noqa: BLE001
        print(f"  (falha na gravação: {exc})")
        return None

    # Always visible, regardless of outcome: this is the fastest way to tell
    # "the mic cut me off early" (short elapsed, stopped by silence) from
    # "I never got heard at all" from a genuinely long, complete take.
    global LAST_STOP_SECONDS
    LAST_STOP_SECONDS = round(elapsed, 2)
    print(f"  (gravação: {elapsed:.1f}s, parou por {stop_reason})")
    if not blocks or not speech_detected:
        return None

    # Confirms "heard you, stop talking" - the direct fix for the earlier
    # duplicate-speech bug, where the player wasn't sure they'd been heard
    # and repeated themselves within the silence-hang window.
    _play_tone(_STOP_TONE_HZ)

    audio = np.concatenate(blocks)
    # Keep audio up to speech_pad_ms past the last block Silero actually
    # classified as speech - not up to where the silence-hang timer happened
    # to run out. This is why trailing-word clipping is structurally reduced
    # rather than just delayed further: the cut point is tied to the model's
    # own last positive classification, not to a fixed guess about how much
    # tail is "probably" safe to keep.
    speech_pad_samples = int(SAMPLE_RATE * speech_pad_ms / 1000.0)
    keep_until = min(len(audio), last_speech_end_sample + speech_pad_samples)
    audio = audio[:keep_until]

    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(_CHANNELS)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm16.tobytes())
    return buf.getvalue()
