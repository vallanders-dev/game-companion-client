"""client/build_assets.py — the Build pane's pictures on this PC (no Qt):
the block icons of the chips and the real screenshots of the steps
(Claude's PR 3b brief, 2026-11-24).

Both come only from the server (client/ is public; the files stay there):
- icons: GET /v1/build_guides/icons/{block}.png — crops of the owner's own
  inventory screenshots, 60x60 PNG, one per block id (without "minecraft:").
  Kept in memory for the run and on disk at %APPDATA%/Parca/build_icons/,
  like the brewing strip's (client/brewing_icons.py): they change only with
  a release, and the second run shows the chips with icons at once.
- shots: GET /v1/build_guides/{id}/shots/{name}.jpg — `layer_<n>` for a step,
  `front` / `side` / `back` for the finished build, prepared by the server
  (cropped, re-encoded, bounded). Kept in memory for the run only: a build is
  re-shot now and then, and 1 MB per open is nothing on a home line. The one
  inside a `guide` message (`shot`) is remembered the same way, so it is
  never fetched twice.

Ids are closed shapes (letters, digits, underscores; a shot name is
`layer_<n>` or an angle) — never a path. A file that isn't a PNG / JPEG (the
signature) or is bigger than an icon / a shot can be is dropped, not shown.
Everything is per item: one missing file never costs the others, and a slow
or absent server just means chips without icons and a step without its
picture (the layer grid stands in).
"""
from __future__ import annotations

import base64
import os
import re
import threading
from pathlib import Path

_ID_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_GUIDE_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_SHOT_RE = re.compile(r"^(front|side|back|layer_[1-9][0-9]?)$")
_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
MAX_ICON_BYTES = 256 * 1024
MAX_SHOT_BYTES = 512 * 1024  # the server's cap is 450 KB
MAX_SHOTS_KEPT = 128  # in memory for the run: a few builds of 5-12 layers plus their angles, ~16 MB at most
ANGLES = ("front", "side", "back")

_lock = threading.Lock()
_icons: dict[str, bytes] = {}
_shots: dict[tuple[str, str], bytes] = {}


def icon_dir() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home()) / "Parca" / "build_icons"


def block_id(block) -> str:
    """"minecraft:oak_planks" -> "oak_planks"; "" when it is not an id."""
    name = str(block or "").strip().lower()
    name = name.split(":", 1)[1] if ":" in name else name
    return name if _ID_RE.match(name) else ""


def icon_ids(build: dict) -> list[str]:
    """Every block id a build's palette uses, once each, in palette order."""
    out: list[str] = []
    palette = build.get("palette") if isinstance(build, dict) else None
    for entry in (palette or {}).values():
        if isinstance(entry, dict):
            bid = block_id(entry.get("block"))
            if bid and bid not in out:
                out.append(bid)
    return out


def _is_png(data) -> bool:
    return isinstance(data, (bytes, bytearray)) and 0 < len(data) <= MAX_ICON_BYTES and bytes(data[:8]) == _PNG


def _is_jpeg(data) -> bool:
    return isinstance(data, (bytes, bytearray)) and 0 < len(data) <= MAX_SHOT_BYTES and bytes(data[:3]) == _JPEG


# ---- icons ------------------------------------------------------------------
def icon_cached(block: str, directory: Path | None = None) -> bytes | None:
    if not _ID_RE.match(str(block or "")):
        return None
    with _lock:
        if block in _icons:
            return _icons[block]
    path = (directory or icon_dir()) / f"{block}.png"
    try:
        data = path.read_bytes() if path.is_file() else None
    except OSError:
        data = None
    if data is not None and _is_png(data):
        with _lock:
            _icons[block] = bytes(data)
        return bytes(data)
    return None


def remember_icon(block: str, data: bytes, directory: Path | None = None) -> bool:
    if not _ID_RE.match(str(block or "")) or not _is_png(data):
        return False
    with _lock:
        _icons[block] = bytes(data)
    try:
        d = directory or icon_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{block}.png.tmp"
        tmp.write_bytes(bytes(data))
        os.replace(tmp, d / f"{block}.png")
    except OSError:
        pass  # memory still has it
    return True


def icons_split(ids: list[str], directory: Path | None = None) -> tuple[dict[str, str], list[str]]:
    """({block: base64 PNG} already here, [blocks still to fetch])."""
    have: dict[str, str] = {}
    missing: list[str] = []
    for bid in ids:
        data = icon_cached(bid, directory)
        if data is not None:
            have[bid] = base64.b64encode(data).decode("ascii")
        elif _ID_RE.match(str(bid or "")):
            missing.append(bid)
    return have, missing


def icons_fetch(session, ids: list[str], directory: Path | None = None, per_icon_timeout: float = 4.0) -> dict[str, str]:
    """Fetches `ids` (session.build_icon), remembers them, returns {block:
    base64 PNG} for the ones that arrived. A 404 (no icon yet for a new
    block) is just a chip with its colour square."""
    out: dict[str, str] = {}
    if session is None:
        return out
    for bid in ids:
        if not _ID_RE.match(str(bid or "")):
            continue
        try:
            data = session.build_icon(bid, timeout=per_icon_timeout)
        except Exception:  # noqa: BLE001 - offline, 404, slow
            continue
        if remember_icon(bid, data, directory):
            out[bid] = base64.b64encode(data).decode("ascii")
    return out


# ---- shots ------------------------------------------------------------------
def valid_shot(guide_id, name) -> bool:
    return isinstance(guide_id, str) and bool(_GUIDE_RE.match(guide_id)) and isinstance(name, str) \
        and bool(_SHOT_RE.match(name))


def shot_cached(guide_id: str, name: str) -> bytes | None:
    with _lock:
        return _shots.get((guide_id, name))


def remember_shot(guide_id: str, name: str, data) -> bool:
    """Keeps a shot (memory). `data` is bytes or the message's base64 string.
    False when it isn't a JPEG under the cap."""
    if not valid_shot(guide_id, name):
        return False
    if isinstance(data, str):
        try:
            data = base64.b64decode(data, validate=True)
        except (ValueError, TypeError):
            return False
    if not _is_jpeg(data):
        return False
    with _lock:
        if len(_shots) >= MAX_SHOTS_KEPT:  # the other builds' shots go first, this build keeps its own (up to 99 layers + 3 angles)
            for key in [k for k in _shots if k[0] != guide_id]:
                del _shots[key]
            if len(_shots) >= MAX_SHOTS_KEPT:
                _shots.clear()
        _shots[(guide_id, name)] = bytes(data)
    return True


def remember_message_shot(body: dict) -> bool:
    """The `shot` inside a `guide` message ({name, mime, image base64}): into
    the cache, so the pane's own clicks never fetch it again."""
    shot = body.get("shot") if isinstance(body, dict) else None
    if not isinstance(shot, dict):
        return False
    return remember_shot(str(body.get("id") or ""), str(shot.get("name") or ""), shot.get("image"))


def shot_names(available: dict | None, layers: int) -> list[str]:
    """What to prefetch after an `open`, in the order the player will need
    it: the steps bottom-up, then the finished build's angles (front first:
    the finish shows it). `available` is the server's {layers, angles} (an
    older server sends none: nothing to fetch)."""
    if not isinstance(available, dict):
        return []
    names: list[str] = []
    got = available.get("layers")
    for n in (got if isinstance(got, list) else []):
        if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= max(1, int(layers or 1)):
            names.append(f"layer_{n}")
    got = available.get("angles")
    for a in (got if isinstance(got, list) else []):
        if a in ANGLES and a not in names:
            names.append(a)
    return [n for n in names if _SHOT_RE.match(n)]


def shots_fetch(session, guide_id: str, names: list[str], on_each=None, per_shot_timeout: float = 6.0,
                still_wanted=None) -> dict[str, str]:
    """Fetches the shots not yet here (session.build_shot), one by one, and
    calls `on_each(name, base64)` as each lands - so layer 2 is on the pane
    before layer 8 has arrived. `still_wanted()` False stops the run (the
    guide closed meanwhile). Returns {name: base64} of the ones that came."""
    out: dict[str, str] = {}
    if session is None or not isinstance(guide_id, str) or not _GUIDE_RE.match(guide_id):
        return out
    for name in names:
        if still_wanted is not None and not still_wanted():
            break
        if not _SHOT_RE.match(str(name or "")):
            continue
        if shot_cached(guide_id, name) is not None:
            continue
        try:
            data = session.build_shot(guide_id, name, timeout=per_shot_timeout)
        except Exception:  # noqa: BLE001 - offline, 404 (no such shot), slow: the grid stands in
            continue
        if remember_shot(guide_id, name, data):
            b64 = base64.b64encode(bytes(data)).decode("ascii")
            out[name] = b64
            if on_each is not None:
                on_each(name, b64)
    return out


def shots_have(guide_id: str, names: list[str]) -> dict[str, str]:
    """{name: base64} of the shots already in memory, for the UI at once."""
    out: dict[str, str] = {}
    for name in names:
        data = shot_cached(guide_id, name)
        if data is not None:
            out[name] = base64.b64encode(data).decode("ascii")
    return out


def clear_memory() -> None:  # tests
    with _lock:
        _icons.clear()
        _shots.clear()
