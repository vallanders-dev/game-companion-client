"""launcher_lookup.py — resolve a real game name from its own launcher's
local install metadata, given the game's running executable's full path.

**Why this exists (2026-09-24):** `game_aliases.json` only recognizes games
someone has manually added an exe-name/title entry for - it can never cover
"whatever game a beta tester, or any PC gamer anywhere, happens to have
installed." Most PC games are installed through Steam or Epic Games, and
both launchers write their own local manifest file for every installed
game containing its real, canonical name - reading that is a deterministic,
zero-network, zero-API-cost way to recognize essentially any Steam/Epic
game without curating it first (Principle 1: deterministic over inferred -
this is about as deterministic as it gets, since it's the game's own
install metadata, not a guess from an exe name).

**Deliberately NOT wired in ahead of the curated alias table** - see
`capture._match_foreground_window()`'s pass 3. A curated `game_aliases.json`
entry (needed for any game with real knowledge_base/ content) always wins
first; this is only consulted when neither the process-name nor the
title-substring pass matches anything curated, so an already-aliased game
costs zero extra file I/O.

**A resolved name here is a real-but-uncurated game name**, handled exactly
like the existing voice-detection free-form fallback
(`main.resolve_active_game_by_voice()`'s free-form path, see CLAUDE.md):
session-scoped only, NEVER auto-written to `game_aliases.json` (Principle 3
- an unconfirmed single lookup becoming a permanent mapping would be a
silent wrong write), and the turn proceeds with the honest "sem anotações"
disclosure since there's no curated content for it (Principle 2) - the same
safe path already used for any known-but-content-free game.

**Steam and Epic are implemented; EA is deliberately NOT.** Steam's
`appmanifest_*.acf` and Epic's `Manifests/*.item` formats are stable and
well-documented by the community (not an official API, but consistent for
years). EA's local manifest format has changed across "Origin" and the
newer "EA app," is far less consistently documented, and there is no EA
install available anywhere in this project's development environment to
verify against - writing a parser for a format that's never been checked
against a real install would be exactly the kind of guess-ahead-of-evidence
this project's other closed lists (`_EXE_NOISE_SUFFIX_RE`,
`GAME_VOICE_MATCH_MIN_RATIO`) explicitly avoid. `ea_game_name()` is a
stub that always returns ``None`` until a real EA install (a tester's
machine, or a live pass on one) can confirm the actual format - add it then,
not now.

**Never raises, never guesses.** Every function here returns ``None`` on
any failure (file missing, unreadable, malformed, no match) - a miss falls
through to `game_aliases.json`'s existing title-substring pass, never a
crash and never a wrong name.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# A Steam-installed game always sits at
# "<some Steam library>/steamapps/common/<installdir>/...(subfolders)/<exe>"
# - true regardless of which drive/library the player chose, so the running
# exe's own path already tells us exactly which steamapps/ directory (and
# therefore which appmanifest_*.acf files) to look at. No need to enumerate
# Steam's `libraryfolders.vdf` - we're not searching FOR a game, we already
# know precisely which one is running.
def _find_steam_installdir(exe_path: Path) -> tuple[Path, str] | None:
    """Returns ``(steamapps_dir, installdir_name)`` if ``exe_path`` runs
    through a ``.../steamapps/common/<installdir>/...`` layout, else
    ``None``."""
    for common_dir in exe_path.parents:
        if common_dir.name.lower() != "common":
            continue
        steamapps_dir = common_dir.parent
        if steamapps_dir.name.lower() != "steamapps":
            continue
        # Walk back down from the exe to the directory that is a DIRECT
        # child of common_dir - that child's name is the installdir, no
        # matter how many subfolders (bin/x64/...) the exe itself sits in.
        child = exe_path
        while child.parent != common_dir:
            if child.parent == child:  # reached a filesystem root - malformed input
                return None
            child = child.parent
        return steamapps_dir, child.name
    return None


# A Steam .acf file is Valve's flat "KeyValues" format, e.g.:
#   "AppState"
#   {
#       "appid"      "1091500"
#       "name"       "Cyberpunk 2077"
#       "installdir" "Cyberpunk 2077"
#   }
# A full VDF parser is unnecessary for reading two flat top-level fields -
# this regex scan is the same technique widely used by community tools for
# exactly this file shape, and keeps this project dependency-free (no VDF
# library to add to requirements.txt).
_ACF_KV_RE = re.compile(r'"([^"]+)"\s*"([^"]*)"')


def _parse_acf_fields(text: str, fields: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in _ACF_KV_RE.findall(text):
        if key in fields and key not in out:  # first occurrence wins, deterministically
            out[key] = value
    return out


def steam_game_name(exe_path: Path) -> str | None:
    """The real Steam store name for the game ``exe_path`` belongs to, read
    from that library's ``appmanifest_*.acf`` files - or ``None`` if
    ``exe_path`` isn't under a Steam ``steamapps/common/`` layout, no
    manifest matches its install directory, or anything can't be read."""
    found = _find_steam_installdir(exe_path)
    if found is None:
        return None
    steamapps_dir, installdir_name = found
    try:
        manifest_paths = list(steamapps_dir.glob("appmanifest_*.acf"))
    except OSError:
        return None
    for manifest_path in manifest_paths:
        try:
            text = manifest_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        fields = _parse_acf_fields(text, ("name", "installdir"))
        if fields.get("installdir", "").strip().lower() == installdir_name.strip().lower():
            name = fields.get("name", "").strip()
            if name:
                return name
    return None


# Epic writes one manifest per installed game here, regardless of which
# drive the game itself lives on - a fixed, well-known location (unlike
# Steam, there's no per-library search needed).
EPIC_MANIFEST_DIR = Path(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests")


def epic_game_name(exe_path: Path, manifest_dir: Path = EPIC_MANIFEST_DIR) -> str | None:
    """The real Epic Games Store display name for the game ``exe_path``
    belongs to, read from Epic's own ``*.item`` JSON manifests - or
    ``None`` if the manifest directory doesn't exist, nothing matches, or
    anything can't be read. ``manifest_dir`` is overridable for testing
    (eval/launcher_lookup_harness.py) - production callers never pass it."""
    try:
        manifest_paths = list(manifest_dir.glob("*.item"))
    except OSError:
        return None
    exe_str = str(exe_path).lower()
    for manifest_path in manifest_paths:
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        install_location = str(data.get("InstallLocation", "")).strip()
        if install_location and exe_str.startswith(install_location.lower()):
            name = str(data.get("DisplayName", "")).strip()
            if name:
                return name
    return None


def ea_game_name(exe_path: Path) -> str | None:  # noqa: ARG001 - see module docstring
    """Stub - see the module docstring's "Steam and Epic are implemented;
    EA is deliberately NOT" section. Always returns ``None``."""
    return None


def launcher_game_name(exe_path: Path) -> str | None:
    """Tries Steam, then Epic, then EA (currently always a miss - see
    ``ea_game_name``), in that order; returns the first real name found, or
    ``None`` if none of them recognize ``exe_path``. Every lookup is local
    and read-only - no network call, no paid API, same cost profile as the
    rest of this module."""
    for finder in (steam_game_name, epic_game_name, ea_game_name):
        try:
            name = finder(exe_path)
        except OSError:
            name = None
        if name:
            return name
    return None
