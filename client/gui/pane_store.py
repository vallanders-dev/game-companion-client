"""client/gui/pane_store.py — what the in-game pane keeps on the player's own PC
(client/gui/game_pane.py; the owner's visual-pane brief, 2026-11-24).

Two small things, nothing else:
- the layout: where the orb was left and how big the pane was made
  (`pane.json`: {"orb": [x, y], "size": [w, h]}), so both come back after a
  restart;
- the pin: the one picture the player chose to keep up while building
  (`pin.json` = title, site, author, source page, kind, mime; `pin.img` = the
  bytes the server sent). Never the question that found it, never a tester
  id (CLAUDE.md: nothing on disk links a question to a player).

The folder is %APPDATA%/Parca, worked out when a function is called (never at
import); every function takes an explicit `path`/`folder` so a test can point
it at a scratch folder. Writes go to a temp file and are swapped in, so a crash
mid-write never leaves half a file. Nothing here imports Qt.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

LAYOUT_NAME = "pane.json"
PIN_NAME = "pin.json"
PIN_IMAGE_NAME = "pin.img"
ITEM_FIELDS = ("title", "site", "author", "source_url", "kind", "mime", "step", "steps", "view")
KINDS = ("idea", "step", "done")  # "done": the finished build, made here when the last layer is done (2026-11-24)
VIEW_MAX = 20
MIMES = ("image/jpeg", "image/png", "image/webp")
MAX_IMAGE_BYTES = 6 * 1024 * 1024  # the server re-encodes to ~1280 px; anything bigger is not ours
TITLE_MAX = 200
TEXT_MAX = 120


def default_dir() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home()) / "Parca"


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _pair(value, lo: int, hi: int) -> list[int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        a, b = int(value[0]), int(value[1])
    except (TypeError, ValueError):
        return None
    if not (lo <= a <= hi and lo <= b <= hi):
        return None
    return [a, b]


# ---------------------------------------------------------------- the layout
def load_layout(path: Path | None = None) -> dict:
    """{"orb": [x, y] | None, "size": [w, h] | None} - whatever was saved and
    is a sane number; the pane checks it against the screen it has now."""
    path = path or default_dir() / LAYOUT_NAME
    raw = _read_json(path)
    return {"orb": _pair(raw.get("orb"), -20000, 20000), "size": _pair(raw.get("size"), 40, 8000)}


def save_layout(path: Path | None = None, *, orb: tuple[int, int] | None = None,
                size: tuple[int, int] | None = None) -> dict:
    """Merges the given values into the file (the other is kept)."""
    path = path or default_dir() / LAYOUT_NAME
    data = load_layout(path)
    if orb is not None:
        data["orb"] = [int(orb[0]), int(orb[1])]
    if size is not None:
        data["size"] = [int(size[0]), int(size[1])]
    try:
        _write_atomic(path, json.dumps(data, ensure_ascii=False).encode("utf-8"))
    except OSError:
        pass
    return data


# ---------------------------------------------------------------- the items
def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def clean_item(raw) -> dict | None:
    """One `visual` item as the pane shows it, or None when it can't be shown:
    not a dict, no title, or an image that isn't bytes of a known type.
    `image` arrives base64 (the server's bytes, through main.py's pipe) or as
    bytes already (the pin file); the result's "image" is always bytes. The
    source page is kept only when it is plain http(s) - nothing else ever
    reaches the browser."""
    if not isinstance(raw, dict):
        return None
    title = _text(raw.get("title"), TITLE_MAX)
    if not title:
        return None
    image = raw.get("image")
    if isinstance(image, str):
        try:
            image = base64.b64decode(image, validate=True)
        except (binascii.Error, ValueError):
            return None
    if not isinstance(image, (bytes, bytearray)) or not image or len(image) > MAX_IMAGE_BYTES:
        return None
    mime = _text(raw.get("mime"), 40).lower() or "image/jpeg"
    if mime not in MIMES:
        return None
    url = str(raw.get("source_url") or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or any(ch.isspace() for ch in url):
        url = ""
    kind = _text(raw.get("kind"), 12).lower()
    item = {
        "title": title,
        "site": _text(raw.get("site"), TEXT_MAX) or (parts.netloc.removeprefix("www.") if url else ""),
        "author": _text(raw.get("author"), TEXT_MAX),
        "source_url": url,
        "kind": kind if kind in KINDS else "idea",
        "mime": mime,
        "image": bytes(image),
    }
    for key in ("step", "steps"):
        try:
            n = int(raw.get(key) or 0)
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            item[key] = n
    view = _text(raw.get("view"), VIEW_MAX)  # "frente" / "lado" / "fundos": one angle of a build with real shots
    if view:
        item["view"] = view
    return item


def item_meta(item: dict) -> dict:
    """The item without its bytes (what goes up to main.py and into turn_start)."""
    return {k: item[k] for k in ITEM_FIELDS if k in item}


# ---------------------------------------------------------------- the pin
def save_pin(folder: Path | None, item: dict) -> bool:
    """Keeps the pinned picture: its meta in pin.json, its bytes in pin.img."""
    folder = folder or default_dir()
    try:
        _write_atomic(folder / PIN_IMAGE_NAME, item["image"])
        _write_atomic(folder / PIN_NAME, json.dumps(item_meta(item), ensure_ascii=False).encode("utf-8"))
        return True
    except (OSError, KeyError, TypeError):
        return False


def load_pin(folder: Path | None = None) -> dict | None:
    """The pinned item (with its bytes), or None when there is none or the
    files don't make a showable item."""
    folder = folder or default_dir()
    meta = _read_json(folder / PIN_NAME)
    if not meta:
        return None
    try:
        image = (folder / PIN_IMAGE_NAME).read_bytes()
    except OSError:
        return None
    return clean_item({**meta, "image": image})


def clear_pin(folder: Path | None = None) -> None:
    folder = folder or default_dir()
    for name in (PIN_NAME, PIN_IMAGE_NAME):
        try:
            (folder / name).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
