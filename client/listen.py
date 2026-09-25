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


def record_until_silence(
    *,
    silence_hang: float = 2.5,
    max_duration: float = 25.0,
    min_duration: float = 0.5,
    vad_threshold: float = 0.5,
    speech_pad_ms: float = 30.0,
    no_speech_timeout: float = 4.0,
) -> bytes | None:
    """Record the mic until it goes quiet, then return WAV bytes.

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
            _play_tone(_START_TONE_HZ)
            print("  🎙️ Escutando...")
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

                prob = vad_model(mono)

                if prob >= vad_threshold:
                    speech_detected = True
                    silence_run = 0.0
                    last_speech_end_sample = elapsed_samples
                else:
                    silence_run += _BLOCK_SECONDS

                if not speech_detected and elapsed >= no_speech_timeout:
                    print(f"  (nada falado em {elapsed:.1f}s)")
                    return None
                if (
                    speech_detected
                    and elapsed >= min_duration
                    and silence_run >= silence_hang
                ):
                    stop_reason = f"{silence_hang:.1f}s de silêncio"
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
