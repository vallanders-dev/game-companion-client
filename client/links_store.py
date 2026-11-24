"""client/links_store.py — the links Ombri sent, kept on the player's own PC for
the chat's Links tab (docs/chat_redesign.md, owner decision 2026-11-23).

What is kept: url, title, site, kind, game, ts. Never the question, never the
answer, never a tester id (CLAUDE.md: nothing on disk links a question to a
player). Only http(s) urls, one entry per url (a link sent again moves to the
top with the new time), the newest MAX_LINKS. Nothing is sent anywhere.

The file is %APPDATA%/Parca/links.json, worked out when a function is called
(never at import), and every function takes an explicit `path` so a test can
point it at a scratch folder. Writes go to a temp file and are swapped in, so
a crash mid-write never leaves half a file.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

MAX_LINKS = 2000
FIELDS = ("url", "title", "site", "kind", "game", "ts")
KINDS = ("video", "link", "image")


def default_path() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home()) / "Parca" / "links.json"


def _clean(raw, game: str | None, now: float) -> dict | None:
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url") or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or any(ch.isspace() for ch in url):
        return None
    title = " ".join(str(raw.get("title") or "").split())[:200]
    if not title:
        return None
    kind = str(raw.get("kind") or "link").strip().lower()
    site = " ".join(str(raw.get("site") or "").split())[:80] or parts.netloc.removeprefix("www.")
    g = " ".join(str(raw.get("game") or game or "").split())[:80]
    ts = raw.get("ts")
    ts = float(ts) if isinstance(ts, (int, float)) else now
    return {"url": url, "title": title, "site": site, "kind": kind if kind in KINDS else "link", "game": g, "ts": ts}


def load(path: Path | None = None) -> list[dict]:
    """Newest first. A missing or broken file is an empty list, never an error."""
    path = path or default_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("links") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    now = time.time()
    out = [c for c in (_clean(x, None, now) for x in items) if c is not None]
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out[:MAX_LINKS]


def _write(links: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"links": [{k: x[k] for k in FIELDS} for x in links]}, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, path)


def add(cards: list, game: str | None, path: Path | None = None) -> list[dict]:
    """Adds a turn's cards; returns the whole list, newest first."""
    path = path or default_path()
    now = time.time()
    new = [c for c in (_clean({**x, "ts": None} if isinstance(x, dict) else x, game, now) for x in cards or [])
           if c is not None]
    links = load(path)
    if not new:
        return links
    seen = {c["url"] for c in new}
    merged = new + [x for x in links if x["url"] not in seen]
    merged = merged[:MAX_LINKS]
    try:
        _write(merged, path)
    except OSError as exc:  # a full disk is not a reason to lose the chat
        print(f"[links] não consegui salvar: {exc}", flush=True)
    return merged


def clear(path: Path | None = None) -> None:
    path = path or default_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
