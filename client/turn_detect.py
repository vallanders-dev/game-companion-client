"""End-of-turn detection: has the player finished talking? (0.1.18, 2026-10-16)

The mic used to stop after a fixed 2.5 s of silence (MIC_SILENCE_HANG) - paid on
every question. Voice products decide with a small model instead: Pipecat's
open Smart Turn v3.2 (BSD-2-Clause, Whisper-Tiny encoder + classifier, 8 MB
int8 ONNX, 23 languages incl. Portuguese 94.5% / English 94.3% on its own test
set) hears from the audio itself - tone, pace, a trailing "e..." - whether the
sentence is finished. listen.record_until_silence() asks it at a few short
pauses (SMART_TURN_CHECKS) and stops as soon as it says "done"; the old
2.5 s stays as the fallback, and anything failing here means exactly that.

The model wants Whisper's log-mel features. The reference code computes them
with `transformers` (not in the tester runtime); `log_mel()` is a numpy port of
WhisperFeatureExtractor(chunk_length=8) checked against it
(eval/smart_turn_harness.py). The model file is downloaded once into
%LOCALAPPDATA%/Parca/models, sha256-pinned, like the voice-command models.
"""
from __future__ import annotations

import hashlib
import os
import threading
import urllib.request
from pathlib import Path

SAMPLE_RATE = 16000
WINDOW_SECONDS = 8
N_FFT = 400
HOP = 160
N_MELS = 80
MODEL_NAME = "smart-turn-v3.2-cpu.onnx"
MODEL_SHA256 = "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f"
MODEL_URL = f"https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/main/{MODEL_NAME}"
# Stricter than the reference code's 0.5: stopping while the player is still talking is the
# costly error. eval/smart_turn_testset.py (Pipecat's labelled test set, our pipeline):
# 0.5 -> early stops pt 4/53, en 4/55; 0.85 -> pt 2/53, en 0/55, still ~83% of finished
# takes caught early (the rest wait the old 2.5 s).
THRESHOLD = 0.85


def models_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Parca" / "models"


def _hz_to_mel(f):
    import numpy as np
    f = np.asarray(f, dtype=np.float64)
    # Slaney scale (librosa htk=False), as transformers' mel_filter_bank(mel_scale="slaney")
    f_sp, min_log_hz, min_log_mel, logstep = 200.0 / 3, 1000.0, 15.0, np.log(6.4) / 27.0
    return np.where(f >= min_log_hz, min_log_mel + np.log(np.maximum(f, 1e-10) / min_log_hz) / logstep, f / f_sp)


def _mel_to_hz(m):
    import numpy as np
    m = np.asarray(m, dtype=np.float64)
    f_sp, min_log_hz, min_log_mel, logstep = 200.0 / 3, 1000.0, 15.0, np.log(6.4) / 27.0
    return np.where(m >= min_log_mel, min_log_hz * np.exp(logstep * (m - min_log_mel)), f_sp * m)


_FILTERS = None


def mel_filters():
    """(201, 80) slaney-normalized triangular filters, 0-8000 Hz."""
    global _FILTERS
    if _FILTERS is None:
        import numpy as np
        fft_freqs = np.linspace(0, SAMPLE_RATE // 2, 1 + N_FFT // 2)
        mel = np.linspace(_hz_to_mel(0.0), _hz_to_mel(SAMPLE_RATE / 2), N_MELS + 2)
        filt = _mel_to_hz(mel)
        diff = np.diff(filt)
        slopes = filt[None, :] - fft_freqs[:, None]
        down = -slopes[:, :-2] / diff[:-1]
        up = slopes[:, 2:] / diff[1:]
        bank = np.maximum(0.0, np.minimum(down, up))
        bank *= (2.0 / (filt[2:N_MELS + 2] - filt[:N_MELS]))[None, :]
        _FILTERS = bank
    return _FILTERS


def prepare(audio):
    """Float audio at 16 kHz -> the last 8 s, zero-padded at the START (reference audio_utils)."""
    import numpy as np
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    n = WINDOW_SECONDS * SAMPLE_RATE
    if len(audio) > n:
        return audio[-n:]
    return np.pad(audio, (n - len(audio), 0))


def log_mel(audio):
    """WhisperFeatureExtractor(chunk_length=8)(audio, do_normalize=True) for an
    already 8 s array: (80, 800) float32."""
    import numpy as np
    x = audio.astype(np.float64)
    x = (x - x.mean()) / np.sqrt(x.var() + 1e-7)  # zero_mean_unit_var_norm
    pad = N_FFT // 2
    x = np.pad(x, (pad, pad), mode="reflect")
    n_frames = 1 + (len(x) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n_frames)[:, None]
    window = np.hanning(N_FFT + 1)[:-1]  # periodic Hann
    spec = np.abs(np.fft.rfft(x[idx] * window, n=N_FFT, axis=1)) ** 2  # (frames, 201)
    mel = spec @ mel_filters()  # (frames, 80)
    logs = np.log10(np.maximum(mel, 1e-10)).T[:, :-1]
    logs = np.maximum(logs, logs.max() - 8.0)
    return ((logs + 4.0) / 4.0).astype(np.float32)


class SmartTurn:
    """Thread-safe enough for one recording at a time (the client never records twice at once)."""

    def __init__(self, path: Path) -> None:
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.inter_op_num_threads = 1
        so.intra_op_num_threads = 2
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self._session = ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])

    def probability(self, audio) -> float:
        """Chance that the speaker has finished, for the whole current take."""
        import numpy as np
        feats = log_mel(prepare(audio))[None, :, :]
        out = self._session.run(None, {"input_features": feats})[0]
        return float(np.asarray(out).reshape(-1)[0])


def ensure_model(base: Path | None = None, fetch=None) -> Path:
    """The model file, downloading and checking it first if needed (raises on failure)."""
    base = base or models_dir()
    target = base / MODEL_NAME
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == MODEL_SHA256:
        return target
    data = (fetch or _download)(MODEL_URL)
    got = hashlib.sha256(data).hexdigest()
    if got != MODEL_SHA256:
        raise ValueError(f"modelo {MODEL_NAME}: sha256 {got[:12]}... não confere")
    base.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(target)
    return target


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Ombri"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


_MODEL: SmartTurn | None = None
_LOADING = threading.Lock()
_FAILED = False


def get_model() -> SmartTurn | None:
    """The loaded model, or None (not downloaded yet, or failed - the fixed
    silence wait is used then). Never blocks: loading happens in
    start_loading()'s background thread."""
    return _MODEL


def start_loading(base: Path | None = None) -> None:
    """Download (first run) and load the model in the background, once."""
    def run() -> None:
        global _MODEL, _FAILED
        with _LOADING:
            if _MODEL is not None or _FAILED:
                return
            try:
                _MODEL = SmartTurn(ensure_model(base))
            except Exception as exc:  # noqa: BLE001 - no network, bad file: the fixed wait stays
                _FAILED = True
                print(f"  (detector de fim de fala indisponível: {type(exc).__name__})", flush=True)

    threading.Thread(target=run, name="smart-turn-load", daemon=True).start()
