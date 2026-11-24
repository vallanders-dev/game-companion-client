"""What the player has been playing, read from files already on this PC, for
the morning report ("Ombri, acorda!"): Steam's own last-played dates and hours
and Minecraft's logs in the default .minecraft folder (the mode of the last
session, the player's own newest advancement).

Since 2026-11-23 (Tony: "HoYoverse and all other game launchers"):
- HoYoverse: the date of each game's own log file in AppData\\LocalLow (only
  the file's date is read, never its lines), for a closed list of games.
- Every other launcher (Epic, Battle.net, EA, Ubisoft, Riot, Xbox, GOG...):
  none of them keeps last-played dates in a file we can trust, so Ombri
  counts what it sees itself - the game it detects in front while it runs
  (`seen()`, from client/main.py), last time and minutes, in
  play_seen.json next to settings.json. Only after the player's yes.

Nothing is read until the player says yes: the first "Ombri, acorda!" asks
(server/session.py), a clear yes sets `"play_history": true` in settings.json, a
clear no sets false and it never asks again. The settings window turns it on or
off. Only the short summary from `summarize()` leaves the PC: game names, a
count, a mode word. Never a server address, a player name or a log line.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path

from client.config import USER_SETTINGS_DIR, _read_user_settings, save_user_settings

REFRESH_SECONDS = 60
RECENT_DAYS = 60         # spoken as "the last two months"
RECENT_MONTHS = 2
STALE_DAYS = RECENT_DAYS # not opened since then: "parado", never both at once
STALE_MIN_MINUTES = 60   # an hour in it: a game merely tried isn't "left untouched"
MAX_STALE = 2
MAX_LOGS = 20
MAX_LOG_BYTES = 8 * 1024 * 1024
MINECRAFT = "Minecraft"
NAMES_PATH = USER_SETTINGS_DIR / "steam_names.json"
SEEN_PATH = USER_SETTINGS_DIR / "play_seen.json"
SEEN_GAP_SECONDS = 120     # two sightings further apart than this aren't one session
SEEN_TICK_SECONDS = 5      # main.py may report every poll; one count per 5 s is plenty
SEEN_SAVE_SECONDS = 60
SEEN_MAX = 200

# (publisher folder under AppData\LocalLow, game folder) -> the game's name as
# client/game_aliases.json writes it, so Ombri's own sightings merge with it.
HOYOVERSE = {
    ("miHoYo", "Genshin Impact"): "Genshin Impact",
    ("miHoYo", "原神"): "Genshin Impact",
    ("Cognosphere", "Star Rail"): "Honkai: Star Rail",
    ("miHoYo", "崩坏：星穹铁道"): "Honkai: Star Rail",
    ("miHoYo", "ZenlessZoneZero"): "Zenless Zone Zero",
    ("miHoYo", "绝区零"): "Zenless Zone Zero",
    ("miHoYo", "Honkai Impact 3rd"): "Honkai Impact 3rd",
    ("miHoYo", "崩坏3"): "Honkai Impact 3rd",
}
HOYOVERSE_LOGS = ("output_log.txt", "Player.log", "Player-prev.log")

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')
# Minecraft's own chat lines (chat.type.advancement.*, assets/minecraft/lang): en, pt_br, pt_pt, es_es/es_mx, es_ar.
_ADVANCEMENT = "(?:" + "|".join((
    "has made the advancement", "has reached the goal", "has completed the challenge",
    "alcançou o progresso", "atingiu o objetivo", "concluiu o desafio", "adquiriu a proeza", "completou o desafio",
    "ha conseguido el progreso", "ha alcanzado el objetivo", "ha completado el desafío",
    "acaba de conseguir el progreso", "acaba de alcanzar el objetivo", "acaba de completar el desafío",
)) + ")"
_CONNECT = "Connecting to "
_LOCAL = ("Starting integrated minecraft server", "Preparing level")


def parse_vdf(text: str) -> dict:
    """Steam's text format, keys lowercased. Tolerant: a broken tail just ends the parse."""
    root: dict = {}
    stack = [root]
    key = None
    for m in _TOKEN.finditer(text):
        if m.group(2) == "{":
            child: dict = {}
            if key is not None:
                stack[-1][key] = child
            stack.append(child)
            key = None
        elif m.group(2) == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif key is None:
            key = m.group(1).lower()
        else:
            stack[-1][key] = m.group(1)
            key = None
    return root


def steam_dir() -> Path | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            path = Path(winreg.QueryValueEx(k, "SteamPath")[0])
        if path.is_dir():
            return path
    except (ImportError, OSError):
        pass
    path = Path(r"C:\Program Files (x86)\Steam")
    return path if path.is_dir() else None


def steam_rows(steam: Path | None) -> list[tuple[str, int, int]]:
    """(appid, last played, total minutes) of what the newest Steam user has played."""
    if steam is None:
        return []
    configs = list((steam / "userdata").glob("*/config/localconfig.vdf"))
    if not configs:
        return []
    newest = max(configs, key=lambda p: p.stat().st_mtime)
    node = parse_vdf(newest.read_text("utf-8", "ignore"))
    for key in ("userlocalconfigstore", "software", "valve", "steam", "apps"):
        node = node.get(key)
        if not isinstance(node, dict):
            return []
    rows = []
    for appid, app in node.items():
        if not isinstance(app, dict):
            continue
        try:
            last, minutes = int(app.get("lastplayed", 0)), int(app.get("playtime", 0))
        except ValueError:
            continue
        if last > 0 and minutes > 0:
            rows.append((appid, last, minutes))
    return rows


def _load_names() -> dict:
    try:
        data = json.loads(NAMES_PATH.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _fetch_name(appid: str) -> dict | None:
    """Steam's public store page data: the app's name and whether it's a game.
    {"type": ""} when Steam no longer lists it; None on a network failure, to try again later."""
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&filters=basic"
    req = urllib.request.Request(url, headers={"User-Agent": "Ombri"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            entry = json.load(resp).get(appid) or {}
    except (OSError, ValueError):
        return None
    data = entry.get("data") if entry.get("success") else None
    return {"name": str(data.get("name", "")), "type": str(data.get("type", ""))} if isinstance(data, dict) else {"type": ""}


def named_games(rows: list[tuple[str, int, int]], fetch=_fetch_name, cache: dict | None = None) -> list[dict]:
    """The rows that Steam says are games, with their names. Tools, soundtracks
    and anything Steam can't name are left out: they'd make the count a lie."""
    names = _load_names() if cache is None else cache
    changed = False
    games = []
    for appid, last, minutes in rows:
        info = names.get(appid)
        if info is None:
            info = fetch(appid)
            if info is None:
                continue
            names[appid] = info
            changed = True
        if info.get("type") == "game" and info.get("name"):
            games.append({"name": re.sub("[\u2122\u00ae\u00a9]", "", info["name"]).strip(), "last": last, "minutes": minutes})
    if changed and cache is None:
        try:
            NAMES_PATH.parent.mkdir(parents=True, exist_ok=True)
            NAMES_PATH.write_text(json.dumps(names), encoding="utf-8")
        except OSError:
            pass
    return games


def _read_log(path: Path) -> str:
    if path.stat().st_size > MAX_LOG_BYTES:
        return ""
    raw = gzip.open(path, "rb").read(MAX_LOG_BYTES) if path.suffix == ".gz" else path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "ignore")  # Windows Java writes its logs in the system code page


def minecraft_summary(folder: Path) -> dict:
    """{"last": time, "mode": "online"|"local"} of the newest log with a
    session (a launcher left at the menu isn't one), and "achievement": the
    player's own newest advancement. Empty when there's nothing to read."""
    logs = folder / "logs"
    if not logs.is_dir():
        return {}
    files = sorted(logs.glob("*.log*"), key=lambda p: p.stat().st_mtime, reverse=True)[:MAX_LOGS]
    out: dict = {}
    me = ""
    for path in files:
        try:
            text = _read_log(path)
        except (OSError, EOFError):
            continue
        found = re.search(r"Setting user: (\S+)", text)
        me = me or (found.group(1) if found else "")
        if "last" not in out:
            joined = text.rfind(_CONNECT)
            hosted = max(text.rfind(s) for s in _LOCAL)
            if joined >= 0 or hosted >= 0:
                out["last"] = int(path.stat().st_mtime)
                out["mode"] = "online" if joined > hosted else "local"
        if me and "achievement" not in out:
            mine = re.findall(rf"\[CHAT\] {re.escape(me)} {_ADVANCEMENT} \[(.+?)\]\s*$", text, re.M)
            if mine:
                out["achievement"] = mine[-1]
        if "last" in out and "achievement" in out:
            break
    return out


def hoyoverse_games(local_low: Path) -> list[dict]:
    """HoYoverse games by the date of their own log file (the file is never opened)."""
    found: dict[str, int] = {}
    for (publisher, folder), name in HOYOVERSE.items():
        for log in HOYOVERSE_LOGS:
            try:
                when = int((local_low / publisher / folder / log).stat().st_mtime)
            except OSError:
                continue
            found[name] = max(found.get(name, 0), when)
    return [{"name": n, "last": t, "minutes": 0, "source": "hoyoverse"} for n, t in found.items()]


def _load_seen(path: Path | None = None) -> dict:
    try:
        data = json.loads((path or SEEN_PATH).read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def seen_games(data: dict | None = None) -> list[dict]:
    """What Ombri itself saw in front, any launcher."""
    data = _load_seen() if data is None else data
    out = []
    for name, rec in data.items():
        if isinstance(name, str) and name.strip() and isinstance(rec, dict):
            try:
                out.append({"name": name, "last": int(rec.get("last", 0)),
                            "minutes": int(float(rec.get("minutes", 0))), "source": "ombri"})
            except (TypeError, ValueError):
                continue
    return [g for g in out if g["last"] > 0]


_seen_lock = threading.Lock()
_seen_state: dict = {"data": None, "game": None, "at": 0.0, "saved": 0.0, "on": None, "checked": 0.0}


def seen(game: str | None, now: float | None = None, path: Path | None = None) -> None:
    """The game client/main.py detected in front just now (None: no game).
    Counts last time and minutes, only while the play report is on. Never
    raises: the voice loop calls it."""
    try:
        _seen(game, time.time() if now is None else now, path)
    except Exception:  # noqa: BLE001 - a missed count never stops a turn
        pass


def _seen(game: str | None, now: float, path: Path | None) -> None:
    st = _seen_state
    with _seen_lock:
        if game == st["game"] and now - st["at"] < SEEN_TICK_SECONDS:
            return
        if st["on"] is None or now - st["checked"] >= SEEN_SAVE_SECONDS:
            st["on"], st["checked"] = enabled(), now
        if not st["on"] or not game:
            st["game"], st["at"] = None, now
            return
        if st["data"] is None:
            st["data"] = _load_seen(path)
        rec = st["data"].setdefault(game, {"last": 0, "minutes": 0.0})
        if st["game"] == game and 0 < now - st["at"] <= SEEN_GAP_SECONDS:
            rec["minutes"] = float(rec.get("minutes", 0)) + (now - st["at"]) / 60.0
        rec["last"] = int(now)
        st["game"], st["at"] = game, now
        if now - st["saved"] >= SEEN_SAVE_SECONDS:
            st["saved"] = now
            _save_seen(st["data"], path or SEEN_PATH)


def _save_seen(data: dict, path: Path) -> None:
    if len(data) > SEEN_MAX:
        for name in sorted(data, key=lambda n: data[n].get("last", 0))[:len(data) - SEEN_MAX]:
            del data[name]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def merge_games(rows: list[dict]) -> list[dict]:
    """One row per game (same name, any case): the newest date, the most
    minutes, and every source it was counted in."""
    by_key: dict[str, dict] = {}
    for g in rows:
        key = " ".join(g["name"].lower().split())
        src = g.get("source", "steam")
        have = by_key.get(key)
        if have is None:
            by_key[key] = {**g, "sources": {src}}
        else:
            have["last"] = max(have["last"], g["last"])
            have["minutes"] = max(have["minutes"], g["minutes"])
            have["sources"].add(src)
    return list(by_key.values())


def summarize(games: list[dict], minecraft: dict, now: float) -> dict | None:
    """The whole payload the server gets; None with nothing to report.
    `games` rows may say their "source" (steam when they don't)."""
    played = merge_games(games)
    if minecraft.get("last"):
        known = next((g for g in played if g["name"] == MINECRAFT), None)
        if known is None:
            played.append({"name": MINECRAFT, "last": minecraft["last"], "minutes": 0, "sources": {"minecraft"}})
        else:
            known["last"] = max(known["last"], minecraft["last"])
            known["sources"].add("minecraft")
    if not played:
        return None
    last = max(played, key=lambda g: g["last"])
    stale = sorted((g for g in played if now - g["last"] > STALE_DAYS * 86400 and g["minutes"] >= STALE_MIN_MINUTES),
                   key=lambda g: g["minutes"], reverse=True)[:MAX_STALE]
    recent = [g for g in played if now - g["last"] <= RECENT_DAYS * 86400]
    out: dict = {
        "recent": len(recent),
        "months": RECENT_MONTHS,
        # Where the count comes from, said aloud: games on Epic or a console aren't in it.
        "counted_in": [src for src in ("steam", "hoyoverse", "ombri", "minecraft")
                       if any(src in g["sources"] for g in recent)],
        "last_game": last["name"],
        "stale": [{"game": g["name"], "since": time.strftime("%Y-%m", time.localtime(g["last"]))} for g in stale],
    }
    if last["name"] == MINECRAFT:
        out["last_mode"] = minecraft.get("mode", "")
    if minecraft.get("achievement"):
        out["achievement"] = {"game": MINECRAFT, "title": minecraft["achievement"]}
    return out


def read_now() -> dict | None:
    games = named_games(steam_rows(steam_dir()))
    local_low = Path(os.environ.get("USERPROFILE") or Path.home()) / "AppData" / "LocalLow"
    folder = Path(os.environ.get("APPDATA") or Path.home()) / ".minecraft"
    return summarize(games + hoyoverse_games(local_low) + seen_games(), minecraft_summary(folder), time.time())


_latest: dict | None = None
_wake = threading.Event()
_started = False


def enabled() -> bool:
    return _read_user_settings().get("play_history") is True


def unasked() -> bool:
    """Neither yes nor no yet: the next morning report asks."""
    return not isinstance(_read_user_settings().get("play_history"), bool)


def _loop() -> None:
    global _latest
    while True:
        _wake.clear()
        try:
            _latest = read_now() if enabled() else None
        except Exception:  # noqa: BLE001 - the report just goes without it
            pass
        _wake.wait(REFRESH_SECONDS)


def start() -> None:
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_loop, name="play-history", daemon=True).start()


def snapshot() -> dict | None:
    """What goes with the next question; None when off (checked now, so turning it
    off in the settings window stops it at once). `first_today`: the long report
    is for the day's first one."""
    if _latest is None or not enabled():
        return None
    return {**_latest, "first_today": _read_user_settings().get("play_history_report_day") != time.strftime("%Y-%m-%d")}


def after_turn(result: dict, sent: dict | None) -> None:
    """The player's answer to the server's question, and that the day's report was given."""
    answer = (result.get("stats") or {}).get("play_history_consent")
    if isinstance(answer, bool):
        save_user_settings(play_history=answer)
        _wake.set()
    if sent and result.get("lane") == "obvious:report" and sent.get("first_today"):
        save_user_settings(play_history_report_day=time.strftime("%Y-%m-%d"))
