"""gui/global_keys.py — hook-free global key detection (GetAsyncKeyState polling).

Why not capture.HotkeyManager (the `keyboard` library): it installs a
WH_KEYBOARD_LL hook whose callback runs Python, and Windows silently
removes a low-level hook whose callback exceeds LowLevelHooksTimeout.
That was the 2026-09-23 hypothesis for F9/F7/Scroll Lock all failing
in-game (Naruto Storm, Minecraft) - and it was NOT confirmed: the hook
survived a 3.7 s GIL stall with a key pressed mid-stall and still fired
afterwards - and the in-game "failures" behind it turned out to be the
mouse's scroll button being pressed instead of the Scroll Lock key
(Scroll Lock confirmed detected live in Minecraft, windowed and
fullscreen). Polling is kept because it has no hook to lose and detects
identically (2/2 on the desktop incl. a 5 ms tap); nothing proves it was
ever NEEDED. Same approach OBS uses for global hotkeys on Windows. No Qt
imports on purpose (tools/hotkey_isolation_test.py uses this).
"""

from __future__ import annotations

import ctypes
import sys

VK_CODES = {"scroll lock": 0x91, "pause": 0x13, **{f"f{i}": 0x6F + i for i in range(1, 25)}}


class AsyncKeyPoller:
    """Duck-types capture.HotkeyManager (available / pressed() / clear()),
    like gamepad.GamepadWatcher does. pressed() must be called from a timer
    (<= 50 ms); it returns True exactly once per physical press."""

    def __init__(self, key: str) -> None:
        self.key = key.strip().lower()
        if self.key not in VK_CODES:
            raise ValueError(f"tecla não suportada: {key!r} (suportadas: {', '.join(VK_CODES)})")
        self._vk = VK_CODES[self.key]
        self._was_down = False
        self._user32 = ctypes.WinDLL("user32") if sys.platform == "win32" else None
        self.available = self._user32 is not None
        if self.available:
            self._user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
            self._user32.GetAsyncKeyState.restype = ctypes.c_short
            self._user32.GetAsyncKeyState(self._vk)  # drain a stale "pressed since last call" bit

    def pressed(self) -> bool:
        if not self.available:
            return False
        state = self._user32.GetAsyncKeyState(self._vk)
        down = bool(state & 0x8000)
        tapped = bool(state & 0x0001)  # a tap shorter than the poll interval
        fired = not self._was_down and (down or tapped)
        self._was_down = down
        return fired

    def clear(self) -> None:
        pass
