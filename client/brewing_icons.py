"""client/brewing_icons.py — the brewing strip's icons on this PC (no Qt).

The server serves the owner's own screenshots, one per item, at
GET /v1/brewing/icons/{id}.png (server/brewing.py; tester auth). main.py
fetches the ones a `recipe` card needs and keeps them:
- in memory for the run;
- on disk at %APPDATA%/Parca/brewing_icons/<id>.png, so the second run
  shows the strip without asking the server (they change only with a
  release; a stale icon is still that item).

Ids are the closed list's shape only: lowercase letters, digits and
underscores, at most 40 characters - never a path. A file that isn't a PNG
(the signature) is dropped, not shown. Everything is fire-and-forget on the
caller's thread: a server that is slow or away just means text chips.
"""
from __future__ import annotations

import base64
import os
import re
import threading
from pathlib import Path

_ID_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_PNG = b"\x89PNG\r\n\x1a\n"
_MAX_BYTES = 256 * 1024  # an icon is a few KB; anything bigger is not one

_lock = threading.Lock()
_memory: dict[str, bytes] = {}


def cache_dir() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home()) / "Parca" / "brewing_icons"


def valid_id(icon_id) -> bool:
    return isinstance(icon_id, str) and bool(_ID_RE.match(icon_id))


def _is_png(data) -> bool:
    return isinstance(data, (bytes, bytearray)) and len(data) <= _MAX_BYTES and bytes(data[:8]) == _PNG


def cached(icon_id: str, directory: Path | None = None) -> bytes | None:
    """The icon's PNG bytes from memory or disk, or None."""
    if not valid_id(icon_id):
        return None
    with _lock:
        if icon_id in _memory:
            return _memory[icon_id]
    path = (directory or cache_dir()) / f"{icon_id}.png"
    try:
        data = path.read_bytes() if path.is_file() else None
    except OSError:
        data = None
    if data is not None and _is_png(data):
        with _lock:
            _memory[icon_id] = bytes(data)
        return bytes(data)
    return None


def remember(icon_id: str, data: bytes, directory: Path | None = None) -> bool:
    """Keeps a fetched icon (memory + disk). False when it isn't a PNG."""
    if not valid_id(icon_id) or not _is_png(data):
        return False
    with _lock:
        _memory[icon_id] = bytes(data)
    try:
        d = directory or cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{icon_id}.png.tmp"
        tmp.write_bytes(bytes(data))
        os.replace(tmp, d / f"{icon_id}.png")
    except OSError:
        pass  # memory still has it
    return True


def icon_ids(card: dict) -> list[str]:
    """Every icon id a recipe card shows (steps, then modifiers), once each."""
    ids: list[str] = []
    for key in ("steps", "modifiers"):
        for s in card.get(key) or [] if isinstance(card, dict) else []:
            icon = s.get("icon") if isinstance(s, dict) else None
            if valid_id(icon) and icon not in ids:
                ids.append(icon)
    return ids


def split(card: dict, directory: Path | None = None) -> tuple[dict[str, str], list[str]]:
    """({id: base64 PNG} already here, [ids still to fetch]) for a card."""
    have: dict[str, str] = {}
    missing: list[str] = []
    for icon_id in icon_ids(card):
        data = cached(icon_id, directory)
        if data is not None:
            have[icon_id] = base64.b64encode(data).decode("ascii")
        else:
            missing.append(icon_id)
    return have, missing


def fetch(session, ids: list[str], directory: Path | None = None, per_icon_timeout: float = 4.0) -> dict[str, str]:
    """Fetches `ids` from the server (session.brewing_icon), remembers them,
    returns {id: base64 PNG} for the ones that arrived. Errors are per icon:
    one missing icon never costs the others."""
    out: dict[str, str] = {}
    if session is None:
        return out
    for icon_id in ids:
        if not valid_id(icon_id):
            continue
        try:
            data = session.brewing_icon(icon_id, timeout=per_icon_timeout)
        except Exception:  # noqa: BLE001 - offline, 404, slow: a chip instead
            continue
        if remember(icon_id, data, directory):
            out[icon_id] = base64.b64encode(data).decode("ascii")
    return out


def clear_memory() -> None:  # tests
    with _lock:
        _memory.clear()
