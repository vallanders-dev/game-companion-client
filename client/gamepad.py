"""Optional controller/gamepad support - a second, additive trigger alongside
the keyboard hotkey (capture.py) for the same screenshot + voice-question flow.

Uses pygame's joystick module (SDL2 underneath). SDL2 ships a community-
maintained controller database (SDL_GameControllerDB) that normalizes button
semantics across Xbox, PlayStation (DirectInput/HID), Switch Pro, and most
generic USB gamepads - verified on this machine: an "Xbox 360 Controller"
reports exactly 11 raw buttons at indices 0-10 that line up one-to-one with
SDL's canonical A,B,X,Y,BACK,GUIDE,START,LEFTSTICK,RIGHTSTICK,LEFTSHOULDER,
RIGHTSHOULDER order (with the d-pad correctly reported as a hat, not buttons
11-14) - i.e. SDL is remapping it, not just exposing raw HID indices. Also
live-press verified with a Switch Pro controller, including with other
windows (not this script) holding OS focus - see README. Not independently
verified against a physical PlayStation pad.

Passive: only reads state via polling, never suppresses or intercepts input,
same non-interference principle as the keyboard hotkey.

Not yet rebindable in-app (deferred) - change GAMEPAD_COMBO via config.json /
env var, same pattern as the keyboard hotkey before its in-app rebind existed.
"""
from __future__ import annotations

import os
import threading
import time

DEFAULT_COMBO = "back+leftshoulder"  # see README for why
_POLL_SECONDS = 0.05
_RESCAN_SECONDS = 1.0  # how often to check for a (re)plugged controller

# SDL_GameController button names -> pygame's CONTROLLER_BUTTON_* constant
# names. Populated lazily once pygame is importable (avoids importing pygame
# at module load just to build this table).
_BUTTON_ALIASES = {
    "a": "A",
    "b": "B",
    "x": "X",
    "y": "Y",
    "back": "BACK",
    "view": "BACK",  # Xbox One+ renamed "Back" to "View"
    "share": "BACK",  # PlayStation naming
    "guide": "GUIDE",
    "xbox": "GUIDE",
    "start": "START",
    "menu": "START",  # Xbox One+ renamed "Start" to "Menu"
    "options": "START",  # PlayStation naming
    "leftstick": "LEFTSTICK",
    "l3": "LEFTSTICK",
    "rightstick": "RIGHTSTICK",
    "r3": "RIGHTSTICK",
    "leftshoulder": "LEFTSHOULDER",
    "lb": "LEFTSHOULDER",
    "l1": "LEFTSHOULDER",
    "rightshoulder": "RIGHTSHOULDER",
    "rb": "RIGHTSHOULDER",
    "r1": "RIGHTSHOULDER",
    "dpup": "DPAD_UP",
    "dpdown": "DPAD_DOWN",
    "dpleft": "DPAD_LEFT",
    "dpright": "DPAD_RIGHT",
}


def normalize_combo(combo) -> tuple[str, ...]:
    """Accepts "back+leftshoulder", "back,leftshoulder", or an iterable."""
    if isinstance(combo, str):
        parts = [p.strip().lower() for p in combo.replace("+", ",").split(",") if p.strip()]
    else:
        parts = [str(p).strip().lower() for p in combo]
    return tuple(parts)


def describe_combo(combo: tuple[str, ...]) -> str:
    return " + ".join(p.upper() for p in combo) if combo else "(nenhum)"


class GamepadWatcher:
    """Background-polls one connected controller for a held button combo.

    Duck-types the same ``clear()`` / ``pressed()`` interface as
    ``capture.HotkeyManager`` so ``capture.prompt_scene()`` can treat both as
    interchangeable triggers.
    """

    def __init__(self, combo: str) -> None:
        self.combo = normalize_combo(combo) or normalize_combo(DEFAULT_COMBO)
        self.available = False
        self.controller_name = ""
        self._event = threading.Event()
        self._stop = threading.Event()
        self._pygame = None
        self._button_ids: dict[str, int] = {}
        self._unknown_buttons = [b for b in self.combo if b not in _BUTTON_ALIASES]
        self._start()

    def _start(self) -> None:
        os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")  # headless: no window
        # This app never has OS focus - the game does. By default SDL only
        # updates joystick state for the focused window, which would make the
        # combo silently never register during actual play (same shape of bug
        # as the keyboard hotkey and exclusive fullscreen). Must be set before
        # pygame.init(); verified with a live controller press while another
        # window held focus - see README "Controller support".
        os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
        try:
            import pygame
        except Exception as exc:  # noqa: BLE001 - not installed, or fails to load
            print(f"  (controle indisponível: {exc})")
            return

        try:
            pygame.init()  # event pump needs a subsystem init; "dummy" video is enough
            pygame.joystick.init()
        except Exception as exc:  # noqa: BLE001
            print(f"  (não consegui iniciar o subsistema de controle: {exc})")
            return

        if self._unknown_buttons:
            print(
                f"  (combo de controle tem botão(ões) desconhecido(s): "
                f"{', '.join(self._unknown_buttons)} - ignorados)"
            )
        self._button_ids = {
            name: getattr(pygame, f"CONTROLLER_BUTTON_{_BUTTON_ALIASES[name]}")
            for name in self.combo
            if name in _BUTTON_ALIASES
        }

        self._pygame = pygame
        self.available = True
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _poll_loop(self) -> None:
        pygame = self._pygame
        joystick = None
        last_scan = 0.0
        while not self._stop.is_set():
            try:
                pygame.event.pump()

                now = time.monotonic()
                if joystick is None and now - last_scan >= _RESCAN_SECONDS:
                    last_scan = now
                    if pygame.joystick.get_count() > 0:
                        joystick = pygame.joystick.Joystick(0)
                        self.controller_name = joystick.get_name() or "controle"

                if joystick is not None and self._button_ids:
                    n = joystick.get_numbuttons()
                    held = all(
                        idx < n and joystick.get_button(idx)
                        for idx in self._button_ids.values()
                    )
                    if held:
                        self._event.set()
            except Exception:  # noqa: BLE001 - e.g. unplugged mid-poll; retry
                joystick = None
                self.controller_name = ""

            time.sleep(_POLL_SECONDS)

    def clear(self) -> None:
        self._event.clear()

    def pressed(self) -> bool:
        return self._event.is_set()
