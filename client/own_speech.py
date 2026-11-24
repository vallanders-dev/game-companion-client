"""client/own_speech.py — Voz and Ouvido with the player's own key (docs/chat_redesign.md,
2026-11-23). Standard library only (urllib): no new package, no new installer.

Order of a turn stays the server's: the question still goes through the
server's checks (distress line, spoiler terms, Jev) as `question_text`, and
the voice reads the answer text the server already checked (`turn_text`).
With an own key the words go straight to that provider, under its rules -
the settings screen says so.

Every call raises OwnSpeechError on any failure; the caller then uses Ombri's
own service for the rest of the turn. Error texts never contain the key.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid

TIMEOUT = 20.0
PCM_RATE = 24_000  # both providers below answer 16-bit mono PCM at 24 kHz = client/speech.py TTS_PCM_RATE

ELEVEN_BASE = "https://api.elevenlabs.io/v1"
ELEVEN_MODEL = "eleven_flash_v2_5"
OPENAI_BASE = "https://api.openai.com/v1"
OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
OPENAI_STT_MODEL = "gpt-4o-mini-transcribe"
OPENAI_VOICES = ("alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer")
STT_LANG = {"pt": "pt", "en": "en", "es": "es"}


class OwnSpeechError(Exception):
    """`code`: why, for the player's own words (client/gui/own_keys_panel.why_text):
    refused | permission | credit | limit | offline | other. `detail`: the missing
    permission's id (permission) or the HTTP status (other). Never the provider's
    own message: OpenAI's 401 text repeats part of the key."""

    def __init__(self, message: str, code: str = "other", detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


_PERM_RE = re.compile(r"missing the permissions?\s+['\"]?([a-z_]+)|missing scopes?:\s*([a-z0-9_.]+)", re.IGNORECASE)


def _reason(status: int, body: bytes) -> tuple[str, str]:
    """(code, detail) from an error answer of ElevenLabs or OpenAI."""
    try:
        data = json.loads(body[:4096] or b"{}")
    except ValueError:
        data = {}
    err = data.get("detail") if isinstance(data.get("detail"), dict) else data.get("error")
    err = err if isinstance(err, dict) else {}
    kind = str(err.get("status") or err.get("code") or err.get("type") or "").lower()
    text = str(err.get("message") or "")
    m = _PERM_RE.search(text)
    perm = (m.group(1) or m.group(2)).rstrip(".") if m else ""
    if "missing_permission" in kind or perm or "insufficient permissions" in text.lower():
        return "permission", perm.lower()
    if "quota" in kind or "credit" in kind or "insufficient_quota" in kind:
        return "credit", ""
    if "invalid_api_key" in kind or status == 401:
        return "refused", ""
    if status == 403:
        return "permission", ""
    if status == 429:
        return "limit", ""
    return "other", str(status)


STREAM_CHUNK = 4800  # 0.1 s of 24 kHz 16-bit mono: the first sound plays before the sentence is done


def _request(url: str, *, key_header: tuple[str, str], data: bytes | None = None,
             content_type: str | None = None, timeout: float = TIMEOUT, on_chunk=None) -> bytes:
    """The answer's body, or - with `on_chunk(pcm) -> bool|None` - each piece of it as
    it arrives (whole 16-bit samples; False from `on_chunk` stops reading), then b""."""
    headers = {key_header[0]: key_header[1], "User-Agent": "Ombri"}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https hosts above
            if on_chunk is None:
                return resp.read()
            odd = b""
            while True:
                piece = resp.read(STREAM_CHUNK)
                if not piece:
                    return b""
                piece = odd + piece
                cut = len(piece) - (len(piece) % 2)
                piece, odd = piece[:cut], piece[cut:]
                if piece and on_chunk(piece) is False:
                    return b""
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except OSError:
            body = b""
        code, detail = _reason(exc.code, body)
        reason = {"refused": "chave recusada", "permission": "chave sem permissão", "credit": "sem créditos",
                  "limit": "limite do serviço"}.get(code, "")
        raise OwnSpeechError(f"HTTP {exc.code}{(' - ' + reason) if reason else ''}"
                             f"{(' (' + detail + ')') if code == 'permission' and detail else ''}",
                             code, detail) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OwnSpeechError(f"sem conexão com o serviço ({type(exc).__name__})", "offline") from None


def _openai(key: str) -> tuple[str, str]:
    return ("Authorization", f"Bearer {key}")


def _eleven(key: str) -> tuple[str, str]:
    return ("xi-api-key", key)


# -- Voz --------------------------------------------------------------------------
def synthesize(provider: str, key: str, voice: str, text: str, on_chunk=None) -> bytes:
    """16-bit mono PCM at PCM_RATE for one piece of the answer. With `on_chunk` the
    audio is streamed (ElevenLabs' /stream): each piece goes to it as it arrives
    and b"" is returned."""
    text = text.strip()
    if not text:
        return b""
    if provider == "elevenlabs":
        if not voice:
            raise OwnSpeechError("escolha uma voz")
        body = json.dumps({"text": text, "model_id": ELEVEN_MODEL}).encode("utf-8")
        path = "/stream" if on_chunk is not None else ""
        return _request(f"{ELEVEN_BASE}/text-to-speech/{urllib.parse.quote(voice)}{path}?output_format=pcm_24000",
                        key_header=_eleven(key), data=body, content_type="application/json", on_chunk=on_chunk)
    if provider == "openai":
        body = json.dumps({"model": OPENAI_TTS_MODEL, "voice": voice if voice in OPENAI_VOICES else "alloy",
                           "input": text, "response_format": "pcm"}).encode("utf-8")
        return _request(f"{OPENAI_BASE}/audio/speech", key_header=_openai(key), data=body,
                        content_type="application/json", on_chunk=on_chunk)
    raise OwnSpeechError(provider)


def voices(provider: str, key: str) -> list[tuple[str, str]]:
    """(id, name) pairs - also the "Testar" button's call (free: no audio made)."""
    if provider == "openai":
        _request(f"{OPENAI_BASE}/models", key_header=_openai(key))
        return [(v, v.capitalize()) for v in OPENAI_VOICES]
    if provider == "elevenlabs":
        try:
            data = json.loads(_request(f"{ELEVEN_BASE}/voices", key_header=_eleven(key)))
        except ValueError:
            raise OwnSpeechError("resposta estranha do serviço") from None
        out = [(str(v.get("voice_id")), str(v.get("name") or v.get("voice_id")))
               for v in data.get("voices", []) if isinstance(v, dict) and v.get("voice_id")]
        if not out:
            raise OwnSpeechError("nenhuma voz nessa conta")
        return out
    raise OwnSpeechError(provider)


# -- Ouvido -----------------------------------------------------------------------
def transcribe(provider: str, key: str, wav: bytes, language: str) -> str:
    if provider != "openai":
        raise OwnSpeechError(provider)
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in (("model", OPENAI_STT_MODEL), ("language", STT_LANG.get(language, "pt"))):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="q.wav"\r\n'
                 f"Content-Type: audio/wav\r\n\r\n".encode() + wav + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    raw = _request(f"{OPENAI_BASE}/audio/transcriptions", key_header=_openai(key), data=b"".join(parts),
                   content_type=f"multipart/form-data; boundary={boundary}", timeout=30.0)
    try:
        return str(json.loads(raw).get("text") or "").strip()
    except ValueError:
        raise OwnSpeechError("resposta estranha do serviço") from None


def test_key(category: str, provider: str, key: str) -> list[tuple[str, str]]:
    """The "Testar" button: one free call. Voz returns its voices; Ouvido []."""
    if category == "voice":
        return voices(provider, key)
    if provider == "openai":
        _request(f"{OPENAI_BASE}/models", key_header=_openai(key))
        return []
    raise OwnSpeechError(provider)
