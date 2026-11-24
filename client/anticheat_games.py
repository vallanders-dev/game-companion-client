"""client/anticheat_games.py - games with an anti-cheat that takes its own screenshots.

Closed list of exe stems (lowercase, no ".exe"). Used by the overlay
(gui/overlay.py `_apply_capture_exclusion`): while one of these is the
foreground window, Ombri's orb is never hidden from screen capture
(WDA_EXCLUDEFROMCAPTURE), whatever the "show Ombri in recordings" setting
says. Hiding a topmost window from capture is the trick cheat overlays use
to stay out of the anti-cheat's own screenshots, so Ombri must not do it next
to one. (2026-11-20, board_2026-11-20_fps_debrief.md follow-up.)

This is NOT the no-screenshot list: that's capture.NO_CAPTURE_EXE_STEMS
(Overwatch, VALORANT), a stricter rule for two games. A wrong stem here only
means a game that never matches; exe names marked (?) aren't checked on a
real install yet.

Only ctypes - the overlay's UI process doesn't load pywin32. The game's
process is opened once per process id (PROCESS_QUERY_LIMITED_INFORMATION,
the weakest right) and the answer cached, not once a second.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import PureWindowsPath

ANTICHEAT_EXE_STEMS = frozenset({
    "valorant-win64-shipping", "valorant",      # Riot Vanguard
    "league of legends",                        # Riot Vanguard
    "overwatch",                                # Blizzard Defense Matrix
    "cod", "cod24-cod",                         # Call of Duty, Ricochet (?)
    "fortniteclient-win64-shipping",            # Easy Anti-Cheat / BattlEye
    "r5apex", "r5apex_dx12",                    # Apex Legends, EAC
    "rainbowsix", "rainbowsix_be", "rainbowsix_vulkan",  # BattlEye
    "tslgame",                                  # PUBG, BattlEye
    "cs2",                                      # VAC
    "dota2",                                    # VAC
    "rustclient",                               # EAC
    "destiny2",                                 # BattlEye
    "escapefromtarkov",                         # BattlEye
    "marvel-win64-shipping",                    # Marvel Rivals, NetEase ACE (?)
    "gta5", "gta5_enhanced",                    # GTA Online, BattlEye since 2024
})

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_stem_by_pid: dict[int, str] = {}


def exe_stem(path: str) -> str:
    return PureWindowsPath(path).stem.lower() if path else ""


def _pid_exe_stem(pid: int) -> str:
    if pid in _stem_by_pid:
        return _stem_by_pid[pid]
    stem = ""
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(len(buf))
                if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    stem = exe_stem(buf.value)
            finally:
                k32.CloseHandle(handle)
    except (OSError, AttributeError, ValueError):
        stem = ""
    if len(_stem_by_pid) > 256:  # pids get reused; keep it small
        _stem_by_pid.clear()
    _stem_by_pid[pid] = stem
    return stem


def foreground_exe_stem() -> str:
    """The foreground window's exe stem, "" when unknown or not on Windows."""
    if sys.platform != "win32":
        return ""
    try:
        user32 = ctypes.WinDLL("user32")
        user32.GetForegroundWindow.restype = wintypes.HWND
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    except (OSError, AttributeError, ValueError):
        return ""
    return _pid_exe_stem(pid.value) if pid.value else ""


def anticheat_game_in_front(stem: str | None = None) -> bool:
    """True when the foreground exe (or `stem`, for tests) is on the list."""
    return (foreground_exe_stem() if stem is None else stem.lower()) in ANTICHEAT_EXE_STEMS
