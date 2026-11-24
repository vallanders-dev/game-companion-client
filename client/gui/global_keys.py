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

Since 2026-11-20 this is also how F8/F6 are read (capture.HotkeyManager
polls first and only falls back to the `keyboard` hook for a key it can't
name): a global WH_KEYBOARD_LL hook sees every keystroke on the PC, the same
thing key-logging cheats install, so it's the one thing anti-cheat is most
likely to object to. Polling installs nothing, reads only the keys it was
given, and never takes the key away from the game (RegisterHotKey would).
"""

from __future__ import annotations

import ctypes
import sys

VK_CODES = {"scroll lock": 0x91, "pause": 0x13, **{f"f{i}": 0x6F + i for i in range(1, 25)}}

# Names as the `keyboard` library writes them (config.json hotkeys predate this).
_NAMED_VK = {
    "space": 0x20, "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "backspace": 0x08,
    "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23, "page up": 0x21, "page down": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "caps lock": 0x14, "num lock": 0x90,
    "print screen": 0x2C,
    **{f"num {d}": 0x60 + d for d in range(10)},
}
# A modifier counts if either side is down (VK_CONTROL etc. already mean "either").
_MODIFIER_VK = {
    "ctrl": (0x11,), "control": (0x11,), "shift": (0x10,), "alt": (0x12,),
    "windows": (0x5B, 0x5C), "win": (0x5B, 0x5C), "cmd": (0x5B, 0x5C),
}


def _modifier(token: str) -> tuple[int, ...] | None:
    for side in ("left ", "right "):
        if token.startswith(side):
            token = token[len(side):]
    return _MODIFIER_VK.get(token)


def _char_vk(ch: str) -> int | None:
    if ch.isascii() and ch.isalnum():
        return ord(ch.upper())
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.WinDLL("user32")
        user32.VkKeyScanW.argtypes = [ctypes.c_wchar]
        user32.VkKeyScanW.restype = ctypes.c_short
        scan = user32.VkKeyScanW(ch)
    except (OSError, AttributeError, TypeError):
        return None
    return None if scan == -1 or (scan & 0xFF) == 0xFF else scan & 0xFF


def parse_combo(combo: str) -> tuple[int, tuple[tuple[int, ...], ...]] | None:
    """`"ctrl+shift+f8"` -> (VK of F8, ((VK_CONTROL,), (VK_SHIFT,))), or None
    when it isn't exactly one known key plus modifiers. The literal "+" key
    alone is allowed; "ctrl++" is not (nobody binds that)."""
    combo = combo.strip().lower()
    tokens = ["+"] if combo == "+" else [t.strip() for t in combo.split("+")]
    if not tokens or any(not t for t in tokens):
        return None
    main, mods = None, []
    for token in tokens:
        mod = _modifier(token)
        if mod is not None and len(tokens) > 1:
            mods.append(mod)
            continue
        vk = VK_CODES.get(token) or _NAMED_VK.get(token) or (_char_vk(token) if len(token) == 1 else None)
        if vk is None or main is not None:
            return None
        main = vk
    return (main, tuple(mods)) if main is not None else None


class AsyncKeyPoller:
    """Duck-types capture.HotkeyManager (available / pressed() / clear()),
    like gamepad.GamepadWatcher does. pressed() must be called from a timer
    (<= 50 ms); it returns True exactly once per physical press.

    A combo fires when its key goes down while its modifiers are held. Extra
    modifiers don't block it: a player holding Shift to sprint still gets F8."""

    def __init__(self, key: str) -> None:
        self.key = key.strip().lower()
        parsed = parse_combo(self.key)
        if parsed is None:
            raise ValueError(f"tecla não suportada: {key!r}")
        self._vk, self._mods = parsed
        self._was_down = False
        self._user32 = ctypes.WinDLL("user32") if sys.platform == "win32" else None
        self.available = self._user32 is not None
        if self.available:
            self._user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
            self._user32.GetAsyncKeyState.restype = ctypes.c_short
            self._user32.GetAsyncKeyState(self._vk)  # drain a stale "pressed since last call" bit

    def _held(self, vk: int) -> bool:
        return bool(self._user32.GetAsyncKeyState(vk) & 0x8000)

    def pressed(self) -> bool:
        if not self.available:
            return False
        state = self._user32.GetAsyncKeyState(self._vk)
        down = bool(state & 0x8000)
        tapped = bool(state & 0x0001)  # a tap shorter than the poll interval
        fired = not self._was_down and (down or tapped)
        self._was_down = down
        if fired and self._mods:
            fired = all(any(self._held(vk) for vk in group) for group in self._mods)
        return fired

    def clear(self) -> None:
        pass
