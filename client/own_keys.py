"""client/own_keys.py — the player's own API keys for Voz (voice) and Ouvido (ears),
kept only on this PC (docs/chat_redesign.md, owner decision 2026-11-23).

A key is encrypted with Windows DPAPI (CryptProtectData, the current user's
scope) before it touches the disk, and decrypted only when a call needs it. It
is never logged, never sent to Ombri's server, never shown again in the
settings (the field says "chave guardada"). On a machine without DPAPI (not
Windows) nothing is saved at all - a key in plain text on disk is worse than no
own key.

The file is %APPDATA%/Parca/own_keys.json, worked out when a function is called
(never at import); every function takes an explicit `path` so a test can point
it at a scratch folder. Shape:

    {"voice": {"on": true, "provider": "elevenlabs", "voice": "<id>", "key": "<base64 DPAPI blob>"},
     "ears":  {"on": false, "provider": "openai", "key": "..."}}
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
from pathlib import Path

CATEGORIES = ("voice", "ears")
# Voz is ElevenLabs only for now (owner, 2026-11-23); own_speech keeps the OpenAI voice for later.
PROVIDERS = {"voice": ("elevenlabs",), "ears": ("openai",)}
_ENTROPY = b"ombri-own-keys-v1"  # ties a blob to this app, not a secret


def default_path() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home()) / "Parca" / "own_keys.json"


# -- DPAPI ---------------------------------------------------------------------
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def available() -> bool:
    return sys.platform == "win32"


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _crypt(data: bytes, protect: bool) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    src, entropy, out = _blob(data), _blob(_ENTROPY), _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    # both calls take the same arguments: (in, description, entropy, reserved, prompt, flags, out)
    ok = fn(ctypes.byref(src), None, ctypes.byref(entropy), None, None, CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(out))
    if not ok:
        raise OSError("DPAPI falhou")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(secret: str) -> str:
    return base64.b64encode(_crypt(secret.encode("utf-8"), True)).decode("ascii")


def unprotect(blob: str) -> str:
    return _crypt(base64.b64decode(blob), False).decode("utf-8")


# -- the file --------------------------------------------------------------------
def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load(path: Path | None = None) -> dict:
    """{category: {"on", "provider", "voice", "has_key"}} - never the key itself."""
    raw = _read(path or default_path())
    out = {}
    for cat in CATEGORIES:
        entry = raw.get(cat)
        entry = entry if isinstance(entry, dict) else {}
        if entry.get("provider", PROVIDERS[cat][0]) not in PROVIDERS[cat]:
            entry = {}  # a provider no longer offered: its key is never sent to another one
        out[cat] = {"on": bool(entry.get("on")) and bool(entry.get("key")), "provider": PROVIDERS[cat][0]
                    if entry.get("provider") is None else entry["provider"],
                    "voice": str(entry.get("voice") or "")[:80], "has_key": bool(entry.get("key"))}
    return out


def save(category: str, *, on: bool, provider: str, voice: str = "", key: str | None = None,
         path: Path | None = None) -> None:
    """`key`: a new key to store (encrypted), "" to forget the stored one, None
    to keep it. Raises OSError when the key can't be protected (no DPAPI)."""
    if category not in CATEGORIES or provider not in PROVIDERS[category]:
        raise ValueError(category)
    path = path or default_path()
    data = _read(path)
    entry = data.get(category)
    entry = entry if isinstance(entry, dict) else {}
    if key is not None:
        key = key.strip()
        if key and not available():
            raise OSError("sem DPAPI: a chave não é guardada")
        entry["key"] = protect(key) if key else ""
    if entry.get("provider") != provider:
        entry["voice"] = ""  # a voice id belongs to one provider
    entry.update(on=bool(on) and bool(entry.get("key")), provider=provider)
    if voice:
        entry["voice"] = voice[:80]
    data[category] = entry
    _write(data, path)


def key_for(category: str, path: Path | None = None) -> str | None:
    """The decrypted key while the category is on, else None. Never log it."""
    entry = _read(path or default_path()).get(category)
    if not isinstance(entry, dict) or not entry.get("on") or not entry.get("key") or not available()             or entry.get("provider", PROVIDERS.get(category, ("",))[0]) not in PROVIDERS.get(category, ()):
        return None
    try:
        return unprotect(entry["key"])
    except (OSError, ValueError):
        return None  # another Windows user's blob, or a broken file: Ombri's own service


def clear(path: Path | None = None) -> None:
    try:
        (path or default_path()).unlink()
    except FileNotFoundError:
        pass
