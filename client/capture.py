"""Global hotkey + screenshot capture (Windows-oriented).

Also holds automatic game detection (foreground window/process matched
against ``game_aliases.json``, falling back to the game's own Steam/Epic
launcher metadata via ``launcher_lookup.py`` for anything uncurated) - see
README "Automatic game detection".

The global hotkey uses the ``keyboard`` library. Its low-level hook can miss key
presses while a game runs in *exclusive* fullscreen - run games in "borderless"
/ "windowed fullscreen" mode so the hotkey is seen. See README.md.

**Screen capture privacy (2026-09-24).** ``capture_screenshot_jpeg()`` used
to grab the whole primary monitor unconditionally - sending anything else
on screen (chat, another app, the desktop) to a third-party vision API on
every single turn. ``capture_game_window_jpeg()`` is the real turn-taking
call sites' entry point now: it crops to the foreground window's own
bounds, and ONLY if that window matches a known ``game_aliases.json``
entry (the exact same matching ``detect_game()`` uses, via
``_match_foreground_window()`` - one lookup, not two, so they can never
disagree). If the foreground window isn't a recognized game, it raises
rather than silently falling back to a whole-monitor grab - the caller's
existing "couldn't capture, continue without a scene" handling (never
blocks the turn) is the failure path, matching every other capture
failure. ``capture_screenshot_jpeg()`` itself is unchanged/still available
for anything that genuinely wants the whole monitor (kept for
``eval/capture_sample.py``'s manual sampling).
"""
from __future__ import annotations

import difflib
import io
import json
import os
import re
import sys
import threading
import time
import unicodedata
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from client import launcher_lookup

# JPEG q80 at 960 px wide, not PNG at 1280: measured 2026-09-26
# (eval/scene_image_size_timing.py, 6 real game screenshots) - ~56 KB vs
# ~1 MB and a median Kimi scene read of 2.49 s vs 3.57 s, with scenes as
# good or better; 640 px started breaking the one-short-phrase reply.
_MAX_WIDTH = 960
_JPEG_QUALITY = 80

_keyboard_mod = None
_keyboard_probed = False
_dpi_awareness_set = False


def _ensure_dpi_aware() -> None:
    """A Python process is NOT DPI-aware by default on Windows, which means
    win32gui.GetWindowRect() on a scaled display (125%/150%/...) returns
    coordinates pre-scaled to the "96 DPI virtual" space - NOT the true
    physical pixels mss's grab() operates in. Cropping to a window's rect
    without this would silently grab the wrong region on any scaled
    display. Idempotent (guarded), called once before the first window-rect
    lookup rather than at import time, since it must never be the reason
    this module fails to import on a machine where it errors for some
    unrelated reason (old Windows, restricted process, ...)."""
    global _dpi_awareness_set
    if _dpi_awareness_set or sys.platform != "win32":
        return
    _dpi_awareness_set = True
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:  # noqa: BLE001 - older Windows (shcore missing) or already set elsewhere
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()  # Vista+ fallback, system-DPI-aware only
        except Exception:  # noqa: BLE001
            pass


class HotkeyUnavailable(RuntimeError):
    """The ``keyboard`` library could not be imported / initialised."""


def _keyboard():
    """Import ``keyboard`` lazily; return the module or ``None``.

    Importing can fail (not installed) or raise at import time on some
    platforms (needs root on Linux), so this never raises.
    """
    global _keyboard_mod, _keyboard_probed
    if not _keyboard_probed:
        _keyboard_probed = True
        try:
            import keyboard as mod

            _keyboard_mod = mod
        except Exception:  # noqa: BLE001 - ImportError or OS-level hook failure
            _keyboard_mod = None
    return _keyboard_mod


# --------------------------------------------------------------------------- #
# Screenshot
# --------------------------------------------------------------------------- #
def capture_screenshot_jpeg(max_width: int = _MAX_WIDTH, region: dict | None = None) -> bytes:
    """Grab ``region`` (an mss-style ``{"left", "top", "width", "height"}``
    dict) if given, else the whole primary monitor, and return downscaled
    JPEG bytes. The real turn-taking code calls `capture_game_window_jpeg()`
    instead (see module docstring); this stays available - with its
    original whole-monitor default - for anything that genuinely wants
    that."""
    import mss
    from PIL import Image

    with mss.mss() as sct:
        # monitors[0] is the whole virtual desktop; [1] is the primary monitor.
        raw = sct.grab(region if region is not None else sct.monitors[1])

    img = Image.frombytes("RGB", raw.size, raw.rgb)
    if img.width > max_width:
        height = round(img.height * max_width / img.width)
        resample = getattr(Image, "Resampling", Image).LANCZOS
        img = img.resize((max_width, height), resample)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Hotkey management
# --------------------------------------------------------------------------- #
def normalize_hotkey(hotkey: str) -> str:
    return hotkey.strip().lower()


def looks_layout_fragile(hotkey: str) -> bool:
    """True for an unmodified single punctuation/symbol key.

    Heuristic (see README): no ``+`` in the combo (i.e. no modifier) AND the sole
    token is a single character that is not a letter or digit. This flags keys
    like ``\\ [ ] ; ' ``` `` whose position shifts between keyboard layouts
    (confirmed to bite on Brazilian ABNT2). Named keys (``f8``, ``space``,
    ``enter``) and plain letters/digits are not flagged.
    """
    combo = normalize_hotkey(hotkey)
    if combo == "+":  # the literal "+" key, alone
        return True
    if not combo or "+" in combo:
        return False
    return len(combo) == 1 and not combo.isalnum()


def read_new_hotkey() -> str:
    """Block until the user presses a key/combo; return e.g. ``"ctrl+shift+s"``."""
    kb = _keyboard()
    if kb is None:
        raise HotkeyUnavailable("a biblioteca 'keyboard' não está disponível")
    return normalize_hotkey(kb.read_hotkey())


class HotkeyManager:
    """Owns the currently-registered global hotkey and its 'pressed' flag.

    Degrades gracefully when ``keyboard`` is unavailable: ``available`` is False,
    ``pressed()`` is always False, and only manual scene entry works.
    """

    def __init__(self, hotkey: str) -> None:
        self._kb = _keyboard()
        self.available = self._kb is not None
        self._event = threading.Event()
        self._handle = None
        self._hotkey = normalize_hotkey(hotkey)
        if self._kb is not None:
            try:
                self._handle = self._kb.add_hotkey(self._hotkey, self._event.set)
            except Exception as exc:  # noqa: BLE001
                print(f"  (não consegui registrar o hotkey '{self._hotkey}': {exc})")

    @property
    def hotkey(self) -> str:
        return self._hotkey

    def clear(self) -> None:
        self._event.clear()

    def pressed(self) -> bool:
        return self._event.is_set()

    def try_rebind(self, hotkey: str) -> tuple[bool, str]:
        """Register ``hotkey`` now. On failure the previous binding stays active.

        Returns ``(ok, error_message)``.
        """
        combo = normalize_hotkey(hotkey)
        if self._kb is None:
            return False, "a biblioteca 'keyboard' não está disponível"
        try:
            new_handle = self._kb.add_hotkey(combo, self._event.set)
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        if self._handle is not None:
            try:
                self._kb.remove_hotkey(self._handle)
            except (KeyError, ValueError):
                pass
        self._handle = new_handle
        self._hotkey = combo
        return True, ""


# --------------------------------------------------------------------------- #
# Scene prompt: race the console against every registered trigger
# --------------------------------------------------------------------------- #
# Opt-in instrumentation for the 2026-09-17 "phantom 'jogo' + self-firing F6"
# report (PROJECT_STATUS "Open threads"). Set DEBUG_INPUT_LOG to a file path
# and prompt_scene() appends, per read, every character code msvcrt hands it
# (including the extended-key 0x00/0xe0 lead byte + the second byte it eats),
# which trigger object is pressed when the loop notices, and what it finally
# returns. Off = a single `os.environ.get` per call, no other cost. The point
# is to capture the RAW console byte stream during a real double-F6-during-
# playback repro on the actual machine - the mechanism has to come from that,
# not from a guess (the harness can't reach the keyboard hook, and an
# isolated WriteConsoleInputW probe already showed F-keys act as conhost
# line-edit/history keys: F6 -> ^Z/EOF in a cooked input(), F3/F8 -> history
# recall; the getwch loop below eats F6 as 0x00,'@', so any leaked *text*
# must come from a cooked input() site or console history, not from here).
_DEBUG_INPUT_LOG = os.environ.get("DEBUG_INPUT_LOG", "").strip()


def _dbg(msg: str) -> None:
    if not _DEBUG_INPUT_LOG:
        return
    try:
        with open(_DEBUG_INPUT_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='milliseconds')} {msg}\n")
    except OSError:
        pass


# Returned as `prompt_scene()`'s `fired` when its `game_changed` callback
# (not a trigger) is what ended the wait - see that parameter's docstring.
# A distinct sentinel object, not a trigger instance, so callers can tell
# it apart from every real trigger with a plain `is` check, same as they
# already do for `remember_manager`/`manager`.
GAME_CHANGED = object()


def prompt_scene(
    triggers: list, prompt: str, *, game_changed: Callable[[], bool] | None = None,
    game_poll_seconds: float = 1.0,
) -> tuple[str, object | None]:
    """Wait for the player to either fire a trigger or type a line.

    ``triggers`` is a list of objects duck-typing ``HotkeyManager`` -
    ``clear()`` and ``pressed()`` - so the keyboard hotkey(s) and the optional
    gamepad combo (``gamepad.GamepadWatcher``) race identically; any one of
    them firing wins. Returns ``(text, fired)``: ``fired`` is whichever
    trigger object won the race (identity-comparable with ``is``, so the
    caller can tell *which* trigger it was - e.g. the ask-hotkey vs. a
    separate remember-hotkey), or ``None`` when the player typed a line
    instead. When a trigger fires, ``text`` is "". On Windows this polls the
    console (``msvcrt``) so a trigger fired from another window (the game) can
    win the race. Elsewhere it falls back to a plain blocking ``input()`` and
    no trigger can interrupt it.

    ``game_changed`` (2026-09-25, optional, Windows-only - the non-Windows
    ``input()`` path below can't poll anything mid-call, same pre-existing
    limitation as it has for triggers): a zero-arg callable the caller
    provides to report "the foreground game is no longer the one this wait
    started with" - typically wrapping ``detect_game()`` against the
    caller's own ``active_game``. Polled at most every ``game_poll_seconds``
    (paced like ``main._wait_for_game_or_trigger()``'s identical poll, not
    every 20ms tick - a miss just re-checks next cycle). On a true result,
    the wait ends immediately with ``("", GAME_CHANGED)``, discarding any
    partially-typed buffer - the same "a real event outranks typed input"
    behavior a trigger firing already has. This exists because this
    function's OWN wait loop was the one place a genuine game switch went
    unnoticed: `main.cmd_ask()`'s top-of-loop `detect_game()` call only
    fires again once THIS wait returns, so a player who quit game A and
    launched game B while sitting idle here got their first question in B
    answered against A's context - `active_game`'s designed miss-tolerance
    (see that variable's comment in cmd_ask()) was never meant to survive
    an indefinite wait with zero re-checks, only a single already-in-
    progress turn. The caller is expected to just re-run its own
    `detect_game()` after this returns - `GAME_CHANGED` only says "look
    again," it doesn't carry the new name, so there's exactly one place
    (the loop's real top-of-iteration check) that ever decides what to
    switch to or how to announce it.
    """
    for t in triggers:
        t.clear()

    if not sys.platform.startswith("win"):
        try:
            return input(prompt).strip(), None
        except EOFError:
            return "", None

    import msvcrt

    _dbg(f"prompt_scene ENTER prompt={prompt!r} pre_pressed="
         f"{[i for i, t in enumerate(triggers) if t.pressed()]}")
    sys.stdout.write(prompt)
    sys.stdout.flush()
    buf: list[str] = []
    last_game_check = time.monotonic()
    while True:
        fired = next((t for t in triggers if t.pressed()), None)
        if fired is not None:
            _dbg(f"prompt_scene FIRED trigger_index={triggers.index(fired)} buf={''.join(buf)!r}")
            sys.stdout.write("\n")
            sys.stdout.flush()
            return "", fired

        if game_changed is not None:
            now = time.monotonic()
            if now - last_game_check >= game_poll_seconds:
                last_game_check = now
                if game_changed():
                    _dbg(f"prompt_scene GAME_CHANGED buf={''.join(buf)!r}")
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return "", GAME_CHANGED

        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\r", "\n"):
                _dbg(f"prompt_scene ENTER-key returns {''.join(buf).strip()!r}")
                sys.stdout.write("\n")
                sys.stdout.flush()
                return "".join(buf).strip(), None
            if ch == "\x03":  # Ctrl+C
                raise KeyboardInterrupt
            if ch in ("\x08", "\x7f"):  # Backspace
                if buf:
                    buf.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            if ch in ("\x00", "\xe0"):  # function / arrow key: eat the 2nd byte
                second = msvcrt.getwch() if msvcrt.kbhit() else ""
                _dbg(f"prompt_scene ate extended-key lead={hex(ord(ch))} second={second!r}")
                continue
            if ch < " ":  # other control chars (Ctrl+<key>, Tab, ...) — ignore
                _dbg(f"prompt_scene ignored control char {hex(ord(ch))}")
                continue
            buf.append(ch)
            _dbg(f"prompt_scene char {ch!r} ({hex(ord(ch))}) buf={''.join(buf)!r}")
            sys.stdout.write(ch)
            sys.stdout.flush()

        time.sleep(0.02)


# --------------------------------------------------------------------------- #
# Automatic game detection
# --------------------------------------------------------------------------- #
def load_game_aliases(path: Path) -> list[dict]:
    """Load the alias table (see README "Automatic game detection").

    Re-read fresh on every call, not cached - same reasoning as
    ``speech.load_keyterms()``: editing the file takes effect on the very
    next turn, no restart needed. Missing/unreadable/malformed file -> empty
    list, so detection simply never matches rather than crashing - same
    fail-soft contract as everything else in this module.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


# Known, closed set of renderer/launcher/platform suffixes real game exes
# ship under (verified real-world case: The Witcher 3's actual GOG/Steam
# build ships BOTH `witcher3.exe` and a separate DX12-renderer
# `witcher3_dx12.exe`; Unreal Engine titles commonly ship
# `<Game>-Win64-Shipping.exe`). Stripped, repeatedly, from a detected
# process name before comparing it to an alias's `process_names` entry -
# see `_normalize_exe_stem()`. Deliberately a closed suffix list stripped
# from the END only, NOT open substring containment: containment would
# make "gow" (God of War 2018's `GoW.exe` stem) match INSIDE "gowr"
# (Ragnarök's own `GoWR.exe` stem) and silently misroute one game's
# detection to the other's alias - the exact collision shape already
# documented below for window-title substrings, now avoided on the exe
# side by construction instead of by list-order luck.
_EXE_NOISE_SUFFIX_RE = re.compile(
    r"[-_](?:dx1[12]|dx9|x64|x86|win64|win32|shipping|steam|epic|gog|launcher)$"
)


def _normalize_exe_stem(name: str) -> str:
    """Lowercase ``name`` with the `.exe` extension and any known noise
    suffix (see `_EXE_NOISE_SUFFIX_RE`) stripped, for exact-equality
    process-name matching in `detect_game()`. Only a RECOGNIZED suffix is
    ever stripped - an unrecognized variant simply doesn't match, which is
    the same safe-failure direction as every other closed list in this
    project (a miss falls through to title matching or no match, never a
    wrong one)."""
    stem = name.strip().lower()
    if stem.endswith(".exe"):
        stem = stem[: -len(".exe")]
    changed = True
    while changed:
        changed = False
        m = _EXE_NOISE_SUFFIX_RE.search(stem)
        if m:
            stem = stem[: m.start()]
            changed = True
    return stem


# --- process lookups (ctypes; pywin32 has no QueryFullProcessImageName) ---
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_TOKEN_QUERY = 0x0008
_TOKEN_ELEVATION = 20  # TOKEN_INFORMATION_CLASS.TokenElevation
_win_api = None


def _kernel32_advapi32():
    global _win_api
    if _win_api is None:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        adv.GetTokenInformation.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        _win_api = (ctypes, wintypes, k32, adv)
    return _win_api


def _process_image_path(pid: int) -> Path | None:
    """Full exe path of `pid`, or None. Opens the process with
    PROCESS_QUERY_LIMITED_INFORMATION - the one right Windows grants a normal
    process on an ELEVATED one. The old PROCESS_QUERY_INFORMATION |
    PROCESS_VM_READ open was refused for every game running as administrator,
    so detection found nothing and the client sat silent (measured 2026-09-26:
    all 10 elevated processes in the session refused the old open, all were
    readable this way)."""
    try:
        ctypes, wintypes, k32, _ = _kernel32_advapi32()
        handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if not k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return None
            return Path(buf.value)
        finally:
            k32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - not Windows, ctypes failure
        return None


def _token_elevated(process_handle) -> bool | None:
    ctypes, wintypes, k32, adv = _kernel32_advapi32()
    token = wintypes.HANDLE()
    if not adv.OpenProcessToken(process_handle, _TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        value = wintypes.DWORD()
        size = wintypes.DWORD()
        if not adv.GetTokenInformation(token, _TOKEN_ELEVATION, ctypes.byref(value), 4, ctypes.byref(size)):
            return None
        return bool(value.value)
    finally:
        k32.CloseHandle(token)


def process_is_elevated(pid: int | None = None) -> bool | None:
    """True if `pid` (default: this process) runs as administrator, None if
    Windows won't say."""
    try:
        _, _, k32, _ = _kernel32_advapi32()
        if pid is None:
            return _token_elevated(k32.GetCurrentProcess())
        handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            return _token_elevated(handle)
        finally:
            k32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return None


def foreground_game_needs_admin(aliases_path: Path) -> bool:
    """True when the foreground window is a recognized game running as
    administrator while this client isn't. Windows (UIPI) then hides every
    key press from the client's keyboard hook while the game has focus, so
    F8/F6 silently do nothing - the first live pass lost a session to it.
    False whenever anything can't be determined (never warn on a guess)."""
    match = _match_foreground_window(aliases_path)
    if match is None:
        return False
    try:
        import win32process

        _, pid = win32process.GetWindowThreadProcessId(match[0])
    except Exception:  # noqa: BLE001
        return False
    return process_is_elevated(pid) is True and process_is_elevated() is False


def _match_foreground_window(aliases_path: Path) -> tuple[int, str] | None:
    """The shared lookup behind `detect_game()` (name only) and
    `get_foreground_game_window_rect()` (also needs the hwnd, to crop
    capture to it) - single source of truth for the matching logic, so
    the two can never disagree about what counts as "the game". See
    `detect_game()` for the two-pass process-name-then-title rationale;
    unchanged here, just returning `(hwnd, canonical)` instead of only
    `canonical`.
    """
    try:
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        title = (win32gui.GetWindowText(hwnd) or "").strip()
        if not hwnd or not title:
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        exe_path = _process_image_path(pid)
        if exe_path is None:
            return None
        process_name = exe_path.name.lower()
    except Exception:  # noqa: BLE001 - pywin32 missing, no window, ...
        return None

    aliases = load_game_aliases(aliases_path)
    process_stem = _normalize_exe_stem(process_name)
    title_lower = title.lower()

    if process_stem:
        for entry in aliases:
            canonical = entry.get("canonical_game")
            if not canonical:
                continue
            if any(_normalize_exe_stem(p) == process_stem for p in entry.get("process_names", [])):
                return hwnd, canonical

    for entry in aliases:
        canonical = entry.get("canonical_game")
        if not canonical:
            continue
        if any(sub.lower() in title_lower for sub in entry.get("window_title_substrings", [])):
            return hwnd, canonical

    # Pass 3 (2026-09-24, see launcher_lookup.py): neither a curated exe nor
    # a curated title matched. Before giving up, ask the game's OWN launcher
    # (Steam/Epic) what it's called, from its local install metadata - this
    # is what lets an uncurated game (anything a beta tester or any PC
    # gamer has installed that this project never explicitly aliased) still
    # get a real name instead of no detection at all. Tried LAST,
    # deliberately, so an already-aliased game never pays for this file I/O
    # and a curated match always wins first. The returned name may not be a
    # game_aliases.json canonical string - see detect_game()'s docstring for
    # why that's safe.
    launcher_name = launcher_lookup.launcher_game_name(exe_path)
    if launcher_name:
        return hwnd, launcher_name

    return None


def detect_game(aliases_path: Path) -> str | None:
    """Best-effort: match the foreground window against ``aliases_path``,
    falling back to the game's own launcher metadata (see below).

    For a CURATED game (one in ``aliases_path``), returns the exact
    ``canonical_game`` string from the matched entry - **never** the raw
    detected window title or process name, to avoid recreating the
    game-key-split bug already fixed for Minecraft (a stray "Minecraft."
    vs "Minecraft" game key splitting one game's notes into two
    invisible-to-each-other buckets). Returns ``None`` on any failure or
    total no-match - pywin32 unavailable, no foreground window, nothing
    recognized at all - so the caller can fall back to the manual "Jogo:"
    prompt unconditionally; this must never raise or block normal use for
    an unrecognized game.

    **Three passes, in order, each only tried if the previous ones missed:**

    1. **Process name, GLOBALLY before any title** (2026-09-18, replacing a
       per-entry check that let an EARLIER alias's title substring win over
       a LATER alias's exact process match). An exe name is a stronger
       signal than a window title - a title can carry a loading-screen
       phrase, an appended location, or "(Not Responding)"; an exe name
       essentially never does. Checks every alias's `process_names`
       (normalized via `_normalize_exe_stem()`, so a renderer-suffixed exe
       like `witcher3_dx12.exe` still matches) before pass 2 checks any
       `window_title_substrings` at all.
    2. **Window title substrings** - a same-entry substring check, iterated
       in alias-file order; order-sensitive for genuine substring
       collisions (see the Ragnarök/"God of War" note in
       `game_aliases.json`), left exactly as documented.
    3. **Launcher metadata** (2026-09-24, see `launcher_lookup.py`) - if
       NEITHER of the above matched anything curated, ask the game's own
       Steam/Epic install metadata what it's called. This is what lets an
       uncurated game - anything a beta tester, or any PC gamer anywhere,
       has installed that was never explicitly aliased - still get a real
       name instead of no detection at all. **The returned name here is
       NOT necessarily a `game_aliases.json` canonical string** - it's a
       real-but-uncurated name, handled exactly like the existing
       free-form voice-detection fallback (session-scoped, never written
       to `game_aliases.json`, honest "sem anotações" disclosure since
       there's no curated content for it - see CLAUDE.md's Principle 2/3).
       This is safe by construction: every caller downstream already
       tolerates "a real game name with zero curated notes" for the exact
       same reason a known-but-content-free curated game already works
       today.
    """
    match = _match_foreground_window(aliases_path)
    return match[1] if match else None


def get_foreground_game_window_rect(aliases_path: Path) -> dict | None:
    """The foreground window's bounds - `{"left", "top", "width", "height"}`,
    mss-ready - IFF it matches a known game alias (`_match_foreground_window()`,
    the SAME matching `detect_game()` uses). `None` on any miss: no match, no
    pywin32, no window, a zero-size rect.

    Uses DWM's DWMWA_EXTENDED_FRAME_BOUNDS, not win32gui.GetWindowRect() -
    confirmed live (2026-09-24) this actually matters, not just theoretical:
    GetWindowRect() on a MAXIMIZED window returned (-8, -8, 1936, 1048) for
    a monitor whose real visible area is 1920x1080 - Windows' invisible
    resize-border padding on a WS_THICKFRAME window, included in
    GetWindowRect() but never actually rendered. DWMWA_EXTENDED_FRAME_BOUNDS
    is DWM's own composited/visual bounds, correct for both maximized and
    normal windows. Falls back to GetWindowRect() only if the DWM call
    itself fails (pre-Windows-8, or DWM off) - still a working crop, just
    with that padding on a maximized window."""
    match = _match_foreground_window(aliases_path)
    if match is None:
        return None
    hwnd, _ = match
    try:
        _ensure_dpi_aware()
        import ctypes

        import win32gui

        DWMWA_EXTENDED_FRAME_BOUNDS = 9

        class _Rect(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        rect = _Rect()
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            ctypes.c_void_p(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)
        )
        if hr == 0:
            left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
        else:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    except Exception:  # noqa: BLE001
        return None
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None
    return {"left": left, "top": top, "width": width, "height": height}


def capture_game_window_jpeg(aliases_path: Path, max_width: int = _MAX_WIDTH) -> bytes:
    """The real turn-taking entry point (see module docstring): screenshots
    ONLY the recognized foreground game window, cropped to its bounds -
    never the whole monitor. Raises RuntimeError if the foreground window
    isn't a recognized game, so the caller's existing try/except (every
    call site already treats a capture failure as "no scene, carry on")
    is the failure path - never a silent whole-monitor fallback."""
    rect = get_foreground_game_window_rect(aliases_path)
    if rect is None:
        raise RuntimeError("nenhum jogo reconhecido em primeiro plano — captura pulada")
    return capture_screenshot_jpeg(max_width=max_width, region=rect)


# --------------------------------------------------------------------------- #
# Voice fallback for game detection (README - "Automatic game detection -
# voice fallback"), 2026-09-18
# --------------------------------------------------------------------------- #
def _normalize_game_text(text: str) -> str:
    """Lowercase, accent-stripped, punctuation-collapsed form of a game
    name or a transcribed spoken answer, for `resolve_spoken_game()` -
    same accent-insensitive posture as `companion.classify_confirmation()`
    (Scribe is not reliable about accents/diacritics)."""
    t = unicodedata.normalize("NFKD", text.strip().lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def resolve_spoken_game(transcript: str, candidates: list[str], min_ratio: float) -> str | None:
    """Deterministically match a transcribed spoken answer to one of
    ``candidates`` - real, already-known game names (typically
    ``known_games(chroma_dir)`` unioned with every ``canonical_game`` in
    ``game_aliases.json`` - see README). **Never returns the raw
    transcript** - matched or not, the only possible return values are one
    of ``candidates`` verbatim, or ``None``. This is the same invariant
    `detect_game()` already documents for auto-detected titles/process
    names, applied here to a spoken answer instead (see the historical
    "Minecraft"/"Minecraft." key-split bug in CLAUDE.md - a transcribed
    name becoming its own new key, unmatched against anything real, would
    be exactly that bug class again, just voice-triggered).

    Every candidate gets a score, in three tiers (a candidate never falls
    to a weaker tier than the strongest rule it satisfies):
      2.0  exact match after `_normalize_game_text()`.
      1.0  one normalized string fully CONTAINS the other - handles a
           full spoken sentence ("to jogando o the witcher 3" contains
           "the witcher 3") as well as a short/partial answer ("witcher"
           is contained BY "the witcher 3"). Safe in both directions
           because it is only ever checked against a short, closed
           candidate list, never free text matching free text.
      <1.0 otherwise, a `difflib.SequenceMatcher` ratio, only counted if
           it clears ``min_ratio`` (a closed, code-decided cutoff - see
           `config.DEFAULT_GAME_VOICE_MATCH_MIN_RATIO`, Principle 4's
           "threshold is a safety mechanism, not a tuning knob" posture).

    The highest-scoring candidate wins **only if it is the sole candidate
    at that score** - a tie at ANY tier resolves to no match. This is not
    hypothetical: an unqualified *"god of war"* answer genuinely ties at
    the containment tier between "God of War (2018)" and "God of War
    Ragnarök" - the SAME collision this project already hit and had to
    fix with alias-file ordering on the window-title side (see
    `game_aliases.json`), just reachable here with no "order" to lean on.
    An early version of this function picked whichever candidate came
    first in iteration order for that case - a silent wrong guess -
    caught before shipping by testing exactly that input; tie-detection
    at every tier, not just the fuzzy one, is the fix. Ambiguous is
    exactly a case not to guess on (Principle 3) - it re-asks once
    (`main.resolve_active_game_by_voice()`), same as an outright miss.
    """
    q = _normalize_game_text(transcript)
    if not q or not candidates:
        return None

    scored: list[tuple[str, float]] = []
    for c in candidates:
        c_norm = _normalize_game_text(c)
        if not c_norm:
            continue
        if c_norm == q:
            score = 2.0
        elif c_norm in q or q in c_norm:
            score = 1.0
        else:
            score = difflib.SequenceMatcher(None, q, c_norm).ratio()
            if score < min_ratio:
                continue
        scored.append((c, score))
    if not scored:
        return None

    top = max(s for _c, s in scored)
    winners = [c for c, s in scored if s == top]
    return winners[0] if len(winners) == 1 else None
