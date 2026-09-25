"""tray.py — Windows system-tray presence for main.py's console.

main.py never imports Qt (see gui/client.py's docstring - a second event
loop competing with the timing-sensitive streamed turn is a risk with no
upside). This uses pystray instead, in its own daemon thread: a tray icon
that lets the player quit without needing to focus a console window to
Ctrl+C it. Icon image is generated at runtime from gui.theme.DARK_THEME's
accent color rather than shipping a separate asset, so the tray can't
drift from the overlay's own token.

**2026-09-25**: the menu used to also have a "Mostrar/Ocultar console"
toggle. Removed after a live test found it didn't work (never diagnosed -
see CLAUDE.md) and, separately, the player's own read on it: a Steam/
Discord-style companion has no reason to ever surface its console, so
there was no good reason to keep chasing that bug for a feature nobody
wanted. `hide_console()` still hides it once at startup; there is no way
back from the tray now. `TRAY=false` remains the full escape hatch for
development (console never hides at all).

Same fire-and-forget posture as gui.client.HudClient: any failure here
(pystray/Pillow missing, no console attached, tray icon creation failing)
just means no tray icon, never an exception into the caller.
"""

from __future__ import annotations

import ctypes
import os
import threading

from client.gui.theme import DARK_THEME

try:
    import pystray
    from PIL import Image, ImageDraw
    _LIBS_AVAILABLE = True
except ImportError:
    _LIBS_AVAILABLE = False

_SW_HIDE = 0
_SW_SHOW = 5


def _console_hwnd() -> int | None:
    if os.name != "nt":
        return None
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    return hwnd or None


def _set_console_visible(visible: bool) -> None:
    hwnd = _console_hwnd()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, _SW_SHOW if visible else _SW_HIDE)


def _make_icon_image():
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    accent = DARK_THEME.accent.lstrip("#")
    rgb = tuple(int(accent[i : i + 2], 16) for i in (0, 2, 4))
    ImageDraw.Draw(img).ellipse((4, 4, size - 4, size - 4), fill=rgb + (255,))
    return img


class TrayIcon:
    def __init__(self, app_name: str = "Companheiro de Jogo") -> None:
        self.available = False
        self._icon = None
        if not _LIBS_AVAILABLE or _console_hwnd() is None:
            return
        try:
            self._icon = pystray.Icon(
                app_name,
                _make_icon_image(),
                app_name,
                menu=pystray.Menu(pystray.MenuItem("Sair", self._quit)),
            )
            threading.Thread(target=self._icon.run, name="tray-icon", daemon=True).start()
            self.available = True
        except Exception:  # noqa: BLE001 - tray is optional, never fatal
            self._icon = None

    def _quit(self, icon, item) -> None:  # noqa: ARG002 - pystray callback signature
        icon.stop()
        # Same abrupt exit as Ctrl+C, which a hidden console can no longer
        # receive - main.py's blocking console-input loop has no cross-thread
        # cancellation point to shut down through more gently.
        os._exit(0)

    def hide_console(self) -> None:
        if self.available:
            _set_console_visible(False)
