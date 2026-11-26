"""What the app knows about the running game BY CODE, sent with every question
as `turn_start.game_context` (the in-game visual pane, PR 1, 2026-11-24; the
owner: "game, edition, version and language are always known, by code"): the
edition, the version and the game's own UI language. Minecraft only for now -
the one game whose answers change between Java and Bedrock and between
versions. Another game adds a reader here, never a second game key: Java and
Bedrock are both the one key "Minecraft" (client/game_aliases.json stays as
it is; see CLAUDE.md on the "Minecraft." key split).

  edition   "java"     javaw.exe / java.exe whose window title says Minecraft
            "bedrock"  Minecraft.Windows.exe (the Store/UWP build and the GDK
                       build since 1.21.120, same exe name)
  version   Java: the `--version` argument of the running process - the
            launcher's version id, normalized to the game's version ("26.3",
            "1.21.4", "26w43a"; "fabric-loader-0.18.2-26.3" -> "26.3";
            "neoforge-21.1.80" names no game version -> nothing). When the
            command line cannot be read (Windows before 8.1, a launcher that
            passes the arguments another way) the version the GAME writes in
            its own title: "Minecraft* 26.3 - Singleplayer".
            Bedrock: the package version of the process (kernel32
            GetPackageFullName: "Microsoft.MinecraftUWP_1.26.5203.0_x64__8wekyb3d8bbwe"
            -> "26.52"; the pre-2026 scheme "1.21.11401.0" -> "1.21.114"), else
            the same number from the exe path under WindowsApps.
  language  the game's own options.txt: Java `lang:pt_br` in the instance's
            --gameDir (only when the command line named it - a launcher
            instance has its own folder, so the default %APPDATA%\\.minecraft
            is never assumed); Bedrock `game_language:pt_BR` under
            %APPDATA%\\Minecraft Bedrock\\Users\\<id>\\games\\com.mojang\\minecraftpe
            (1.21.120+), else the old LocalState folder of the Store package.
            Lower-cased with an underscore: "pt_br".

Only those three short values leave the PC - never a path, never the command
line. The command line of a Java game carries the player's session token
(--accessToken), username, UUID and XUID: `pick_java_args()` keeps the two
values it needs (the version id, the game folder - the folder only to find
options.txt) and drops the rest at once; nothing of it is logged or stored.
options.txt is read for its one key and nothing else (Bedrock's also holds the
player's name). Unknown = None, and the server tells Ombri what it does not
know (Principle 2: no guessing). Every reader is best-effort and never raises.

Nothing here reads the game's memory (owner, 2026-10-13: files on disk, never
game memory; and it is what an anti-cheat flags). The game is opened only with
PROCESS_QUERY_LIMITED_INFORMATION, the one right capture._process_image_path()
already uses: the command line comes from the kernel's own copy of it
(NtQueryInformationProcess, ProcessCommandLineInformation - Windows 8.1+, works
on an elevated game, no ReadProcessMemory), the package name from
GetPackageFullName. Never PROCESS_VM_READ.

The Windows calls live in the small `_command_line()` / `_package_full_name()`
/ `_read_text()` / `_bedrock_options_path()` readers at the bottom; everything
above them is pure and runs in eval/game_context_harness.py on recorded
command lines, package names, titles and files.
"""
from __future__ import annotations

import os
import re
import struct
import time
from datetime import date
from pathlib import Path

MINECRAFT = "Minecraft"
BEDROCK_EXES = ("minecraft.windows.exe",)
JAVA_EXES = ("javaw.exe", "java.exe")
REFRESH_SECONDS = 60.0  # the process is read again at most this often while it is the same process
VALUE_MAX = 32
OPTIONS_MAX_BYTES = 256 * 1024  # options.txt is a few KB; a bigger file is not the game's
COMMAND_LINE_MAX_BYTES = 64 * 1024  # the launcher's line is 4-10 KB (the class path)

# Java version ids. Release "26.3" / "1.21.4" / "26.3.1", pre-release "26.3-pre2",
# release candidate "26.3-rc1", snapshot "26w43a".
_RELEASE_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?(?:-(?:pre|rc)\d+)?$")
_SNAPSHOT_RE = re.compile(r"^\d\dw\d\d[a-z]$")
# the title the game writes: "Minecraft 26.3", "Minecraft* 1.21.1 - Singleplayer" (the
# asterisk is the game's own mark for a modded client)
_TITLE_RE = re.compile(r"^Minecraft\*?\s+(\S+)")
# the package full name / WindowsApps folder: "..._1.26.5203.0_x64__8wekyb3d8bbwe"
_PACKAGE_VERSION_RE = re.compile(r"_(\d+)\.(\d+)\.(\d+)\.(\d+)_")
_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?$")
_OPTIONS_KEYS = {"java": "lang", "bedrock": "game_language"}
_WINDOWS_APPS = ("microsoft.minecraftuwp_", "microsoft.minecraftwindowsbeta_")


# --------------------------------------------------------------------------- pure


def exe_name_of(exe_path) -> str:
    """The file name of a path written with either separator ("" for None)."""
    return str(exe_path or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def edition_of(exe_name: str | None, title: str) -> str | None:
    """"bedrock" for Minecraft.Windows.exe; "java" for a Java process whose
    window says Minecraft; "java" too when Windows refused the exe path (a
    race while it starts) but the title carries a version - only the Java
    game writes one there. Anything else is unknown."""
    name = (exe_name or "").strip().lower()
    if name in BEDROCK_EXES:
        return "bedrock"
    if name in JAVA_EXES:
        return "java" if "minecraft" in (title or "").lower() else None
    if not name and version_from_title(title):
        return "java"
    return None


def is_version(token: str) -> bool:
    token = (token or "").strip()
    return bool(token) and len(token) <= VALUE_MAX and bool(_RELEASE_RE.match(token) or _SNAPSHOT_RE.match(token))


def _minecraft_like(token: str, year: int) -> bool:
    """A version that can be Minecraft's own and not a loader's: "1.x" (2011-2025),
    "26.x" and on (the year scheme since 2026, up to next year's), or a snapshot.
    Fabric Loader is 0.x, Forge 47.x/54.x, NeoForge 21.x - none of them is."""
    if _SNAPSHOT_RE.match(token):
        return True
    first = int(token.split(".", 1)[0])
    return first == 1 or 26 <= first <= year % 100 + 1


def java_version_from_id(version_id: str | None, year: int | None = None) -> str | None:
    """The game's version inside a launcher version id, or None. A plain id is
    itself; a loader id names it among other numbers ("fabric-loader-0.18.2-26.3",
    "1.21.1-forge-52.0.40", "1.20.1-OptiFine_HD_U_I6", "quilt-loader-0.26.0-1.21.1");
    an id without it ("neoforge-21.1.80", "MeuPack") is None - the title is next."""
    vid = (version_id or "").strip()
    if not vid or len(vid) > 200:
        return None
    year = year or date.today().year
    if is_version(vid):
        return vid if _minecraft_like(vid, year) else None
    found = [t for t in re.split(r"[-_ ]+", vid) if is_version(t) and _minecraft_like(t, year)]
    return found[0] if found else None


def version_from_title(title: str) -> str | None:
    """"Minecraft 26.3 - Singleplayer" / "Minecraft* 1.21.1" -> the version; None
    for a bare "Minecraft" (Bedrock's title) or anything else."""
    m = _TITLE_RE.match((title or "").strip())
    if not m:
        return None
    token = m.group(1).strip()
    return token if is_version(token) else None


def bedrock_version_from_package(name: str | None) -> str | None:
    """The game's version from a package full name or a WindowsApps path:
    "_1.26.5203.0_" -> "26.52" (the year scheme: 1.<yy>.<nn><rev>.0, checked on
    1.26.4201.0 = 26.42 and 1.26.2101.0 = 26.21), "_1.21.11401.0_" -> "1.21.114",
    "_1.16.22002.0_" -> "1.16.220", "_1.11.402.0_" -> "1.11.4". A shape this code
    does not know is None - a number is never made up."""
    m = _PACKAGE_VERSION_RE.search(name or "")
    if not m:
        return None
    major, minor, build, _revision = (int(g) for g in m.groups())
    if major != 1:
        return None
    patch = build // 100
    return f"{minor}.{patch}" if minor >= 26 else f"1.{minor}.{patch}"


def bedrock_version_from_path(exe_path: Path | None) -> str | None:
    """The same number from the exe's folder under WindowsApps
    ("...\\Microsoft.MinecraftUWP_1.26.5203.0_x64__8wekyb3d8bbwe\\Minecraft.Windows.exe"),
    for when the package name cannot be read; None for any other folder (the
    GDK build installed by the Xbox app sits in C:\\XboxGames, no number there)."""
    parts = str(exe_path or "").replace("\\", "/").rstrip("/").split("/")
    folder = parts[-2] if len(parts) >= 2 else ""
    if not folder.lower().startswith(_WINDOWS_APPS):
        return None
    return bedrock_version_from_package(folder)


def split_command_line(line: str) -> list[str]:
    """Windows-style tokens: spaces split, double quotes group (the class
    path and a game folder with a space in it come quoted)."""
    tokens: list[str] = []
    current: list[str] = []
    quoted = False
    for ch in line:
        if ch == '"':
            quoted = not quoted
        elif ch.isspace() and not quoted:
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(ch)
    if current:
        tokens.append("".join(current))
    return tokens


def pick_java_args(command_line: str | None) -> tuple[str | None, str | None]:
    """(version id, game folder) out of a Java game's command line - the ONLY
    two things taken from it. Both None when either is missing. "--version=x"
    and "--version x" are both the launcher's shapes over the years."""
    if not command_line:
        return None, None
    wanted = {"--version": None, "--gameDir": None}
    tokens = split_command_line(command_line[:COMMAND_LINE_MAX_BYTES])
    for i, token in enumerate(tokens):
        for key in wanted:
            if token == key and i + 1 < len(tokens) and wanted[key] is None:
                wanted[key] = tokens[i + 1]
            elif token.startswith(key + "=") and wanted[key] is None:
                wanted[key] = token[len(key) + 1:]
    version_id = (wanted["--version"] or "").strip() or None
    game_dir = (wanted["--gameDir"] or "").strip() or None
    return version_id, game_dir


def language_from_options(text: str | None, edition: str | None) -> str | None:
    """The one language key of the edition's options.txt (Java "lang:pt_br",
    Bedrock "game_language:pt_BR"), lower-cased with an underscore; None when
    absent or not a language code. Nothing else in the file is read."""
    key = _OPTIONS_KEYS.get(edition or "")
    if not text or not key:
        return None
    for line in text.splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip() == key:
            value = value.strip().strip('"').strip()
            if value and len(value) <= VALUE_MAX and _LANGUAGE_RE.match(value):
                return value.replace("-", "_").lower()
            return None
    return None


def context_of(exe_path: Path | None, title: str, command_line: str | None = None,
               package_full_name: str | None = None, options_text: str | None = None,
               year: int | None = None) -> dict | None:
    """The three values from recorded inputs (what the readers below return);
    None when the window is not a Minecraft edition this code knows. Pure -
    the harness drives this one."""
    edition = edition_of(exe_name_of(exe_path), title)
    if edition is None:
        return None
    version = None
    if edition == "java":
        version_id, _game_dir = pick_java_args(command_line)
        version = java_version_from_id(version_id, year) or version_from_title(title)
    elif edition == "bedrock":
        version = bedrock_version_from_package(package_full_name) or bedrock_version_from_path(exe_path)
    return {"edition": edition, "version": version, "language": language_from_options(options_text, edition)}


def unicode_string_text(raw: bytes, base_address: int, pointer_size: int) -> str | None:
    """The text of the UNICODE_STRING NtQueryInformationProcess wrote into
    `raw` (a buffer that lives at `base_address`): the header (Length,
    MaximumLength, Buffer) with the UTF-16 characters right after it in the
    same buffer. None when the header points anywhere else or is empty -
    nothing outside `raw` is ever read. Pure; the harness drives it with both
    header layouts (16 bytes on a 64-bit Python, 8 on a 32-bit one)."""
    header = "<HH4xQ" if pointer_size == 8 else "<HHI"
    header_size = struct.calcsize(header)
    if len(raw) < header_size:
        return None
    length, _maximum, address = struct.unpack_from(header, raw, 0)
    offset = address - base_address
    if not length or offset < header_size or offset + length > len(raw):
        return None
    return raw[offset:offset + length].decode("utf-16-le", "ignore") or None


# ---------------------------------------------------------------- the live path

_CACHE: dict = {"key": None, "value": None, "at": 0.0}


def context_for(game: str, pid: int | None, exe_path: Path | None, title: str, now: float | None = None) -> dict | None:
    """`turn_start.game_context` for the matched game window, or None (another
    game; nothing known). Read once per process and again every
    REFRESH_SECONDS (the language can change mid-session). Never raises."""
    if (game or "").strip().lower() != MINECRAFT.lower() or not pid:
        return None
    key = (pid, str(exe_path or "").lower())
    now = time.monotonic() if now is None else now
    if _CACHE["key"] == key and _CACHE["value"] is not None and now - _CACHE["at"] < REFRESH_SECONDS:
        return dict(_CACHE["value"])
    try:
        value = _read(pid, exe_path, title)
    except Exception:  # noqa: BLE001 - best effort: a reader slip must never cost the question
        value = None
    _CACHE.update(key=key, value=value, at=now)
    return dict(value) if value else None


def _read(pid: int, exe_path: Path | None, title: str) -> dict | None:
    edition = edition_of(exe_name_of(exe_path), title)
    if edition is None:
        return None
    command_line = package = options = None
    if edition == "java":
        command_line = _command_line(pid)
        _version_id, game_dir = pick_java_args(command_line)
        options = _read_text(_java_options_path(game_dir))  # None without the folder: no guessed file
    else:
        package = _package_full_name(pid)
        path = _bedrock_options_path()
        options = _read_text(path) if path else None
    return context_of(exe_path, title, command_line, package, options)


def _java_options_path(game_dir: str | None) -> Path | None:
    """The instance's options.txt, or None when the command line named no
    --gameDir: the default %APPDATA%\\.minecraft may be another instance's
    (a launcher pack has its own folder), and a wrong language is worse than
    none (Principle 2)."""
    return Path(game_dir) / "options.txt" if game_dir else None


def _bedrock_options_path() -> Path | None:
    """The newest options.txt of the Bedrock data folders this PC has: the
    per-account folders of 1.21.120+ (two roots are seen in the wild), then
    the Store package's LocalState (and the Preview's)."""
    roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    found: list[Path] = []
    for root in (roaming / "Minecraft Bedrock" / "Users", local / "Minecraft Bedrock" / "Users"):
        try:
            found.extend(root.glob("*/games/com.mojang/minecraftpe/options.txt"))
        except OSError:
            continue
    for package in ("Microsoft.MinecraftUWP_8wekyb3d8bbwe", "Microsoft.MinecraftWindowsBeta_8wekyb3d8bbwe"):
        found.append(local / "Packages" / package / "LocalState" / "games" / "com.mojang" / "minecraftpe" / "options.txt")
    newest, newest_time = None, -1.0
    for path in found:
        try:
            when = path.stat().st_mtime
        except OSError:
            continue
        if when > newest_time:
            newest, newest_time = path, when
    return newest


def _read_text(path: Path | None) -> str | None:
    """The file's text (UTF-8, bad bytes dropped), or None - never raises."""
    if path is None:
        return None
    try:
        if path.stat().st_size > OPTIONS_MAX_BYTES:
            return None
        return path.read_bytes().decode("utf-8", "ignore")
    except OSError:
        return None


# ------------------------------------------------------------- Windows readers

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000  # the only right the game is ever opened with here
_PROCESS_COMMAND_LINE_INFORMATION = 60  # NtQueryInformationProcess class: the kernel's copy of the command line
_APPMODEL_ERROR_NO_PACKAGE = 15700


def _command_line(pid: int) -> str | None:
    """The command line of `pid` as the kernel keeps it (NtQueryInformationProcess,
    ProcessCommandLineInformation: Windows 8.1+; the limited open works on an
    elevated game and on a 32-bit or 64-bit target alike), or None when
    Windows won't say. No handle with PROCESS_VM_READ, no ReadProcessMemory:
    the game's memory is never read. The string stays inside this call's
    caller (`pick_java_args()`) and is never logged - it carries the player's
    session token."""
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll")
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        ntdll.NtQueryInformationProcess.restype = ctypes.c_long  # NTSTATUS
        ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG,
                                                    ctypes.POINTER(wintypes.ULONG)]
        handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            needed = wintypes.ULONG(0)
            # first call: the size (STATUS_INFO_LENGTH_MISMATCH with `needed` filled in)
            ntdll.NtQueryInformationProcess(handle, _PROCESS_COMMAND_LINE_INFORMATION, None, 0, ctypes.byref(needed))
            size = needed.value
            if not size or size > COMMAND_LINE_MAX_BYTES:
                return None
            buf = ctypes.create_string_buffer(size)
            if ntdll.NtQueryInformationProcess(handle, _PROCESS_COMMAND_LINE_INFORMATION, buf, size,
                                               ctypes.byref(needed)) != 0:
                return None
            return unicode_string_text(buf.raw, ctypes.addressof(buf), ctypes.sizeof(ctypes.c_void_p))
        finally:
            k32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - not Windows, Windows 8 or older, ctypes failure
        return None


def _package_full_name(pid: int) -> str | None:
    """The package full name of a packaged process ("Microsoft.MinecraftUWP_1.26.5203.0_x64__8wekyb3d8bbwe"),
    None for an unpackaged one or when Windows won't say. The limited open
    works on an elevated game too (same as capture._process_image_path)."""
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.GetPackageFullName.restype = wintypes.LONG
        k32.GetPackageFullName.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR]
        handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            size = wintypes.UINT(0)
            rc = k32.GetPackageFullName(handle, ctypes.byref(size), None)
            if rc == _APPMODEL_ERROR_NO_PACKAGE or not size.value:
                return None
            buf = ctypes.create_unicode_buffer(size.value + 1)
            if k32.GetPackageFullName(handle, ctypes.byref(size), buf) != 0:
                return None
            return buf.value or None
        finally:
            k32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return None
