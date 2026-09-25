"""client/speech.py — local playback only: no API calls, no keys.

Ported from root ``speech.py`` per the approved plan's file-move table -
``AudioOut``/``play()``/the WAV helpers stay client-side permanently,
unchanged in logic. Everything that used to call ElevenLabs/Scribe
directly (``synthesize()``, ``TtsStream``, ``transcribe()``,
``load_keyterms()``) now lives server-side (``server/speech.py``) - this
process only ever receives PCM bytes over the wire (``client/net.py``)
and writes them to the speaker, or plays a cached fixed-line MP3.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

TTS_PCM_FORMAT = "pcm_24000"
TTS_PCM_RATE = 24_000


def write_pcm_wav(pcm: bytes, out_path: Path, rate: int = TTS_PCM_RATE) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm)
    return out_path


def read_pcm_wav(path: Path) -> bytes:
    """Frames of a WAV written by write_pcm_wav(). Refuses anything that
    isn't the exact PCM layout the shared output stream plays."""
    with wave.open(str(path), "rb") as wf:
        if (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) != (1, 2, TTS_PCM_RATE):
            raise ValueError(f"{path.name}: não é PCM mono 16-bit {TTS_PCM_RATE} Hz")
        return wf.readframes(wf.getnframes())


# Written to the device in slices this long so no thread ever holds
# AudioOut.lock for more than one slice's worth of playback, which is what
# lets interrupt() (barge-in) get the lock - and stop the audio - within
# ~100 ms instead of after the whole chunk that was being written.
_WRITE_SLICE_BYTES = TTS_PCM_RATE * 2 // 10  # 100 ms of mono int16


class AudioOut:
    """One sounddevice raw-PCM output stream (mono int16, TTS_PCM_RATE).

    Everything spoken during a turn - the optional filler and the answer
    audio (now arriving as ``turn_seq``-prefixed binary frames over the
    WebSocket instead of from a local ``TtsStream`` - see
    ``client/net.py``) - goes through this one object, so the two can
    never overlap: the filler/answer ORDER is decided under ``lock``
    (``answer_started`` / ``filler_in_progress``) and the loser waits on
    ``filler_done`` rather than interleaving. Writes go to the device in
    100 ms slices, each slice under ``lock``; ``close()`` and
    ``interrupt()`` take the same lock, so the PortAudio stream can never
    be stopped or closed underneath a live ``Pa_WriteStream`` (that race
    was a real native segfault on 2026-09-15 - there is exactly one lock
    and every teardown path goes through it). ``write()`` back-pressures
    on the device buffer (~180 ms measured), which is what paces the
    player thread.

    ``interrupt()`` is barge-in: abort (drops what the device has buffered)
    + close, and every later write is a no-op. A ``cancel`` event passed to
    ``write()`` makes a long write give up between slices.
    """

    def __init__(self, rate: int = TTS_PCM_RATE) -> None:
        self.rate = rate
        self.lock = threading.Lock()
        self._stream = None
        self._closed = False
        self.interrupted = False
        self.interrupted_at: float | None = None  # perf_counter when the device was actually stopped
        self.bytes_written = 0
        self.first_write_at: float | None = None  # time.perf_counter()
        # Filler/answer ordering state - read and written ONLY under `lock`
        # (the events themselves are thread-safe; the decision isn't).
        self.answer_started = threading.Event()
        self.filler_in_progress = False
        self.filler_done = threading.Event()

    # -- lifecycle ------------------------------------------------------------
    def open(self) -> None:
        with self.lock:
            self._open_locked()

    def _open_locked(self) -> None:
        if self._stream is not None or self._closed:
            return
        import sounddevice as sd

        self._stream = sd.RawOutputStream(samplerate=self.rate, channels=1, dtype="int16")
        self._stream.start()

    @property
    def is_open(self) -> bool:
        return self._stream is not None and not self._closed

    def write(self, pcm: bytes, cancel: threading.Event | None = None) -> bool:
        """Write ``pcm`` in slices; returns False if it stopped early because
        the stream was closed/interrupted or ``cancel`` was set. Takes the
        lock per slice - callers must NOT hold it themselves."""
        if not pcm:
            return True
        for i in range(0, len(pcm), _WRITE_SLICE_BYTES):
            if cancel is not None and cancel.is_set():
                return False
            with self.lock:
                if self._closed:
                    return False
                self._open_locked()
                if self.first_write_at is None:
                    self.first_write_at = time.perf_counter()
                piece = pcm[i:i + _WRITE_SLICE_BYTES]
                self._stream.write(piece)
                self.bytes_written += len(piece)
        return True

    def close(self, drain: bool = True) -> None:
        """``drain=True`` lets everything already written finish playing
        (PortAudio's stop() waits for pending buffers); ``False`` cuts it."""
        with self.lock:
            self._closed = True
            self._teardown_locked(abort=not drain)

    def interrupt(self) -> None:
        """Barge-in: stop what is playing NOW (drop the device's buffered
        audio) and refuse further writes. Same lock, same teardown as
        close() - not a second path around it. Idempotent."""
        with self.lock:
            if not self.interrupted:
                self.interrupted = True
                self._closed = True
                self._teardown_locked(abort=True)
                self.interrupted_at = time.perf_counter()

    def _teardown_locked(self, abort: bool) -> None:
        st, self._stream = self._stream, None
        if st is None:
            return
        try:
            if abort:
                st.abort()
            else:
                st.stop()
            st.close()
        except Exception:  # noqa: BLE001 - playback teardown is best-effort
            pass


def play(path: Path) -> None:
    """Play the mp3 out loud. Never fatal - the file is on disk regardless."""
    try:
        from playsound3 import playsound

        playsound(str(path))
        return
    except Exception:
        pass  # fall through to an OS-level player

    try:
        if sys.platform.startswith("win"):
            import os

            os.startfile(str(path))  # type: ignore[attr-defined]  # Windows only
        elif sys.platform == "darwin":
            subprocess.run(["afplay", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception as exc:  # noqa: BLE001
        print(f"  (não consegui tocar o áudio automaticamente: {exc})")
