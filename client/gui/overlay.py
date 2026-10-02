"""gui/overlay.py — the HUD window itself.

Wired into the real voice loop since 2026-09-23 via gui/client.py's
HudClient - see CLAUDE.md's gui/ row for the current module map.

2026-09 restyle: collapsed from three visual states (orb/pill/card) down
to two, orb (idle) vs. expanded card (any active turn).

2026-09-25, second restyle: a persistent reactive orb replaced the flat
ring+icon look, and the card became an opt-in "info panel" the player
toggled open by clicking the orb.

**2026-09-25, third restyle, same day - the panel is gone.** Testing the
second restyle surfaced two things: the player's own click habit opened
the panel without meaning to (any plain click toggled it), and once
opened, live feedback was blunt: "the box that appears with the details
of the question and the answers, I am dropping that... I would like to
have only the sphere itself." The whole info panel - state label, game
name, question/answer captions, the click-to-open/close mechanism, the
window resize animation between orb and panel sizes, the Mica/Acrylic
backdrop toggle that only ever mattered for the panel - is deleted, not
just hidden. This window is now a fixed-size reactive orb plus, since a
follow-up request the same day, one small number: `_paint_usage()` draws
a beta tester's own remaining-calls-today count as a pill below the orb
(`set_usage()` is no longer a no-op - it's the one piece of the old panel
that came back, by request, not a partial walk-back of the rest). `state`
alone still drives the orb's color/motion, and the window still never
resizes - the usage pill's row is baked into the fixed geometry from
construction, not toggled or animated in. `set_game()` and `set_state()`'s
`question`/`answer` parameters are still accepted no-ops purely so
gui/client.py's JSON protocol and main.py's hooks don't need to change -
there is nothing to display that information in. See README.md's "Visual
overlay (HUD)" section: the full text view is a real, working capability
that might come back in a future version - it's defaulted away here, not
designed out of the architecture.

Window behavior, in one place since it's easy to lose track of which flag
does what:
- Frameless / translucent / always-on-top / no taskbar entry:
  FramelessWindowHint + WindowStaysOnTopHint + Qt.Tool, WA_TranslucentBackground.
- WA_ShowWithoutActivating + NoFocus so `show()` never steals keyboard focus
  from the game.
- The orb is painted by hand (paintEvent), not QSS - QSS can't paint a
  gradient circle on a top-level widget's own translucent background the
  way a child frame can.
- Click-through (WA_TransparentForMouseEvents) is toggled by a *global*
  hotkey via gui.global_keys.AsyncKeyPoller (GetAsyncKeyState polling, NOT
  the `keyboard` library's hook - see that module for why) — see
  CLICK_THROUGH_HOTKEY below for which key.
- Idle-only presence fade (Wispr-style, see PRESENCE_* constants below):
  purely timer/activity-driven, not hover-to-reveal - deliberately, since
  once click-through is on the cursor belongs to the game and this
  window never sees it move.

2026-09-19: the click-through hotkey was F9 originally (not F6/F8 -
main.py's remember/ask triggers; not part of GAMEPAD_COMBO's default
chord; not a bare-symbol key per capture.py's ABNT2 warning). A live
investigation over a real game found F9 specifically failing to
register there while working everywhere else - the working theory
(F5/F9 being a common quicksave/quickload convention some engines claim
via their own competing low-level hook) was treated as settled enough to
stop chasing. The fix was switching keys, not further diagnosis - see
CLICK_THROUGH_HOTKEY.

2026-09-23: RESOLVED. The reported in-game Scroll Lock "failures" were a
mix-up (the mouse's middle/scroll button was being pressed, not the
Scroll Lock key). With the real key, detection was confirmed live in
Minecraft, windowed AND fullscreen (tools/hotkey_isolation_test.py).
The earlier F9 failures are unexplained but confounded: before the
2026-09-19 WS_EX_TRANSPARENT fix, click-through never applied even when
the key WAS detected, so "doesn't work" then couldn't tell detection
from application. The quicksave-key theory was never confirmed either.
Also fixed along the way: the toggle had no visible cue while the orb
showed (now a dashed ring). Visibility over fullscreen Minecraft was
broken (the game's own HWND_TOPMOST window covered the orb) and is fixed
by _reassert_topmost() - confirmed live 2026-09-23. A game that takes
the display in true exclusive mode would still hide any window.
"""

from __future__ import annotations

import ctypes
import re
import sys

from enum import Enum

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

from client.gui.global_keys import AsyncKeyPoller
from client.gui.orb import breathing_scale, draw_sphere, state_color
from client.gui.theme import DARK_THEME, Theme


class HudState(Enum):
    IDLE = "idle"
    LISTENING = "ouvindo"
    THINKING = "pensando"
    SPEAKING = "falando"
    SEARCHING = "pesquisando"
    PAUSED = "pausado"


# --------------------------------------------------------------------- #
# Layout / sizing
# --------------------------------------------------------------------- #
ORB_OUTER_DIAMETER = 88

# Usage readout (2026-09-25): a beta tester's own remaining-calls-today
# count, painted as a small pill below the orb - not a return of the
# question/answer info panel (still deleted, see the module docstring),
# just this one number a tester genuinely needs to self-monitor with.
# Widens/heightens the fixed window (still never resizes at runtime) to
# make room; drawn directly in paintEvent(), no child widget, same as the
# orb itself. Window width is computed from REAL font metrics against
# USAGE_WIDTH_SAMPLE at construction time, not a guessed pixel constant -
# a fixed guess (60px extra) measured 40px too narrow the first time this
# was tried, clipping the pill to the window's edge.
USAGE_ROW_HEIGHT = 20
USAGE_ROW_GAP = 6
USAGE_FONT_POINT_SIZE = 8.5
USAGE_PILL_PADDING = 20
USAGE_WIDTH_SAMPLE = "9999 restantes hoje"  # a safely-wide sample - real caps are 3 digits at most today

CORNER = "top-right"          # "top-right" | "top-left" | "bottom-right" | "bottom-left"
CORNER_SCREEN_MARGIN = 24

# Scroll Lock is the established convention for exactly this kind of
# overlay software (Fraps/OBS/ShareX-style capture/HUD toggles) and is
# essentially never claimed by games. (The 2026-09-19 "F9 eaten by the
# game" theory behind this switch was not borne out - see module
# docstring - but Scroll Lock is still the better default.) Must be a
# key in gui.global_keys.VK_CODES. Some compact/laptop keyboards have no dedicated
# Scroll Lock (or Pause) key; if that's the case here, F7 is a documented
# fallback - not risk-free either, just the least commonly claimed
# function key. Defined ONCE, here - overlay.py's own registration and
# gui/demo.py's reference this constant rather than each hardcoding the
# key string, so changing it again later is a one-line edit.
CLICK_THROUGH_HOTKEY = "scroll lock"
HOTKEY_POLL_MS = 50

# Fullscreen games (Minecraft/GLFW confirmed live 2026-09-23) make their own
# window HWND_TOPMOST too; among topmost windows the most recent one wins,
# so the game covers an overlay started before it. Re-asserting our own
# topmost slot on a timer (no activation, no focus change) puts us back on
# top. Cannot help against true exclusive-display fullscreen.
TOPMOST_REASSERT_MS = 1000

# The orb is the approved mockup's sphere since 2026-09-29 (client/gui/orb.py,
# shared with the main window): it breathes at rest and pulses while
# listening/thinking/speaking. ~30fps; canned motion, not mic amplitude.
# (It was a glow with waveform bars / orbiting dots from 2026-09-25.)
ORB_ANIM_MS = 33
ORB_SPHERE_RADIUS = 32  # the mockup's 64 px in-game orb, centred in ORB_OUTER_DIAMETER

# --------------------------------------------------------------------- #
# Presence opacity (Wispr-style idle fade).
# Named constants on purpose: exactly what a live session will retune.
# --------------------------------------------------------------------- #
OPACITY_ATIVO = 1.00
OPACITY_REPOUSO = 0.55
OPACITY_AUSENTE = 0.22
REPOUSO_DELAY_MS = 8_000        # ativo -> repouso
AUSENTE_DELAY_MS = 90_000       # repouso -> ausente (measured from the same contract event, not from repouso's start)
OPACITY_ANIMATION_MS = 500

_RGBA_RE = re.compile(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)")

# --------------------------------------------------------------------- #
# Win32 extended window style helpers (2026-09-19 bugfix session).
#
# Two things Qt's own attributes turned out NOT to reliably cover on this
# frameless/translucent/Tool-flagged top-level window, both confirmed by
# direct GetWindowLongPtr/GetForegroundWindow inspection, not guessed:
#
# 1. WA_ShowWithoutActivating + NoFocus block KEYBOARD focus, but not OS
#    foreground ACTIVATION - a click was still activating the window,
#    which pauses whatever game is underneath. WS_EX_NOACTIVATE is the
#    actual Win32-level fix; there's no Qt attribute for it.
# 2. WA_TransparentForMouseEvents sets Qt's OWN bookkeeping correctly
#    (testAttribute() reports True) but never actually reached
#    WS_EX_TRANSPARENT on GWL_EXSTYLE for this window - checked directly
#    before/after toggling it, byte-identical. Likely because Qt only
#    translates that attribute into the native style at window-creation
#    time for a custom-flagged top-level widget like this one, not on a
#    later runtime toggle. Fixed the same way: set the bit directly.
# --------------------------------------------------------------------- #
_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_NOACTIVATE = 0x08000000


def _get_ex_style(hwnd: ctypes.c_void_p) -> int:
    user32 = ctypes.WinDLL("user32")
    user32.GetWindowLongPtrW.restype = ctypes.c_longlong
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE)


def _set_ex_style(hwnd: ctypes.c_void_p, value: int) -> None:
    user32 = ctypes.WinDLL("user32")
    user32.SetWindowLongPtrW.restype = ctypes.c_longlong
    user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_longlong]
    user32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, ctypes.c_longlong(value))


def _parse_color(value: str) -> QColor:
    """Accepts either `rgba(r, g, b, a)` or a plain hex/name color."""
    match = _RGBA_RE.match(value.strip())
    if not match:
        return QColor(value)
    r, g, b, a = match.groups()
    color = QColor(int(r), int(g), int(b))
    color.setAlphaF(float(a))
    return color


def _hud_key(state: "HudState") -> str:
    return state.value


class OverlayWindow(QWidget):
    """The HUD itself: a fixed-size reactive orb, nothing else (2026-09-25).
    See the module docstring for how the text info panel that used to live
    here was removed, not just hidden."""

    SHADOW_MARGIN = 20  # transparent bleed around the painted orb, for the glow's own falloff to fade into

    def __init__(self, theme: Theme = DARK_THEME, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.theme = theme
        self.state = HudState.IDLE
        self._anchor_corner = CORNER
        self._native_setup_done = False
        self._click_through = False
        self._drag_offset = None
        self._presence_tier = "ativo"  # "ativo" | "repouso" | "ausente"
        self._anim_phase = 0.0  # seconds, free-running - drives the orb's breathing/pulse/swirl (2026-09-25)
        self._usage_text: str | None = None  # None until set_usage() is first called - nothing painted until then

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        usage_font = self.font()
        usage_font.setPointSizeF(USAGE_FONT_POINT_SIZE)
        sample_w = QFontMetrics(usage_font).horizontalAdvance(USAGE_WIDTH_SAMPLE) + USAGE_PILL_PADDING

        orb_footprint = ORB_OUTER_DIAMETER + 2 * self.SHADOW_MARGIN
        w = max(orb_footprint, sample_w)
        h = orb_footprint + USAGE_ROW_GAP + USAGE_ROW_HEIGHT
        self.setGeometry(self._initial_geometry(w, h))

        self._repouso_timer = QTimer(self)
        self._repouso_timer.setSingleShot(True)
        self._repouso_timer.timeout.connect(self._enter_repouso)

        self._ausente_timer = QTimer(self)
        self._ausente_timer.setSingleShot(True)
        self._ausente_timer.timeout.connect(self._enter_ausente)

        self._hotkey_manager = AsyncKeyPoller(CLICK_THROUGH_HOTKEY)
        self._hotkey_poll = QTimer(self)
        self._hotkey_poll.timeout.connect(self._poll_click_through_hotkey)
        self._hotkey_poll.start(HOTKEY_POLL_MS)

        self._topmost_timer = QTimer(self)
        self._topmost_timer.timeout.connect(self._reassert_topmost)
        self._topmost_timer.start(TOPMOST_REASSERT_MS)

        # Continuous, cheap: ~30fps of trig math + a repaint of an 88px
        # window is negligible next to the game rendering underneath it.
        # Runs always, same always-on posture as the hotkey and
        # topmost-reassert timers above - not paused during "ausente"
        # presence, since the orb (however faint) should still read as
        # alive rather than freezing.
        self._orb_anim_timer = QTimer(self)
        self._orb_anim_timer.timeout.connect(self._on_orb_anim_tick)
        self._orb_anim_timer.start(ORB_ANIM_MS)

    def _on_orb_anim_tick(self) -> None:
        self._anim_phase += ORB_ANIM_MS / 1000.0
        self.update()

    def _reassert_topmost(self) -> None:
        if sys.platform != "win32" or not self.isVisible():
            return
        try:
            HWND_TOPMOST = ctypes.c_void_p(-1)
            SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_NOOWNERZORDER = 0x1, 0x2, 0x10, 0x200
            user32 = ctypes.WinDLL("user32")
            user32.SetWindowPos.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
            ]
            user32.SetWindowPos(
                ctypes.c_void_p(int(self.winId())), HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
            )
        except (OSError, AttributeError, ValueError):
            pass

    # ------------------------------------------------------------------ #
    # Public API (also what demo.py drives)
    # ------------------------------------------------------------------ #
    def set_state(self, state: HudState, question: str | None = None, answer: str | None = None) -> None:
        """`question`/`answer` are accepted only for API compatibility with
        gui/client.py's JSON protocol (main.py's hooks still send them) -
        there is no text display any more to put them in. See the module
        docstring."""
        self.state = state
        if state == HudState.IDLE:
            self._start_presence_cycle()
        else:
            self._stop_presence_timers()
            self.setWindowOpacity(OPACITY_ATIVO)
        self.update()

    def set_game(self, name: str | None) -> None:
        """No-op (2026-09-25) - see the module docstring."""

    def set_usage(self, calls_made: int, cap: int) -> None:
        """The one piece of the old info panel that came back (2026-09-25),
        by request - a beta tester's own remaining-calls-today count,
        painted as a small pill below the orb. Not the question/answer
        panel (still gone, see the module docstring) - just this number."""
        from client.gui.ui_texts import questions  # uses -> estimated questions
        self._usage_text = f"~{questions(cap - calls_made)} restantes hoje"
        self.update()

    def set_click_through(self, enabled: bool) -> None:
        self._click_through = enabled
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, enabled)
        self._apply_click_through_native(enabled)
        applied = self.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        native = self._native_transparent_bit() if sys.platform == "win32" else None
        print(f"[overlay] clique-atravessa -> {enabled} (Qt attr={applied}, WS_EX_TRANSPARENT nativo={native})")
        self.update()

    def _apply_click_through_native(self, enabled: bool) -> None:
        if sys.platform != "win32":
            return
        try:
            hwnd = ctypes.c_void_p(int(self.winId()))
            current = _get_ex_style(hwnd)
            new_style = (current | _WS_EX_TRANSPARENT) if enabled else (current & ~_WS_EX_TRANSPARENT)
            _set_ex_style(hwnd, new_style)
        except (OSError, AttributeError, ValueError):
            pass

    def _native_transparent_bit(self) -> bool:
        try:
            hwnd = ctypes.c_void_p(int(self.winId()))
            return bool(_get_ex_style(hwnd) & _WS_EX_TRANSPARENT)
        except (OSError, AttributeError, ValueError):
            return False

    def debug_set_presence_tier(self, tier: str) -> None:
        """Dev/demo-only override — jumps straight to a presence tier's
        opacity without waiting out its timer."""
        self._repouso_timer.stop()
        self._ausente_timer.stop()
        self._presence_tier = tier
        target = {"ativo": OPACITY_ATIVO, "repouso": OPACITY_REPOUSO, "ausente": OPACITY_AUSENTE}[tier]
        self._animate_opacity(target)

    # ------------------------------------------------------------------ #
    # Fixed window geometry (the orb never resizes)
    # ------------------------------------------------------------------ #
    def _initial_geometry(self, w: int, h: int) -> QRect:
        screen = QApplication.primaryScreen().availableGeometry()
        corner = self._anchor_corner
        if corner == "top-right":
            x, y = screen.right() - w - CORNER_SCREEN_MARGIN, screen.top() + CORNER_SCREEN_MARGIN
        elif corner == "top-left":
            x, y = screen.left() + CORNER_SCREEN_MARGIN, screen.top() + CORNER_SCREEN_MARGIN
        elif corner == "bottom-right":
            x, y = screen.right() - w - CORNER_SCREEN_MARGIN, screen.bottom() - h - CORNER_SCREEN_MARGIN
        else:  # bottom-left
            x, y = screen.left() + CORNER_SCREEN_MARGIN, screen.bottom() - h - CORNER_SCREEN_MARGIN
        return QRect(x, y, w, h)

    # ------------------------------------------------------------------ #
    # Presence opacity (idle-only Wispr-style fade)
    # ------------------------------------------------------------------ #
    def _start_presence_cycle(self) -> None:
        self._presence_tier = "ativo"
        self.setWindowOpacity(OPACITY_ATIVO)
        self._repouso_timer.start(REPOUSO_DELAY_MS)
        self._ausente_timer.start(AUSENTE_DELAY_MS)

    def _stop_presence_timers(self) -> None:
        self._repouso_timer.stop()
        self._ausente_timer.stop()

    def _enter_repouso(self) -> None:
        self._presence_tier = "repouso"
        self._animate_opacity(OPACITY_REPOUSO)

    def _enter_ausente(self) -> None:
        self._presence_tier = "ausente"
        self._animate_opacity(OPACITY_AUSENTE)

    def _animate_opacity(self, target: float) -> None:
        self._opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._opacity_anim.setDuration(OPACITY_ANIMATION_MS)
        self._opacity_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._opacity_anim.setStartValue(self.windowOpacity())
        self._opacity_anim.setEndValue(target)
        self._opacity_anim.start()

    # ------------------------------------------------------------------ #
    # One-time native window setup
    # ------------------------------------------------------------------ #
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._native_setup_done:
            self._native_setup_done = True
            self._apply_corner_rounding()
            self._apply_noactivate()
            self._apply_capture_exclusion()

    def _apply_capture_exclusion(self) -> None:
        """The overlay must never itself appear in a screenshot sent to
        Kimi vision (capture.capture_game_window_jpeg() crops to the game
        window, which can overlap the overlay's own corner) or in anything
        else that captures the screen - SetWindowDisplayAffinity with
        WDA_EXCLUDEFROMCAPTURE (Windows 10 2004+) makes the window invisible
        to screen/window capture while remaining normally visible on the
        real display. One-time, same silent-failure posture as the other
        ctypes calls here - an older Windows version just means the overlay
        IS visible in capture, not a broken window."""
        if sys.platform != "win32":
            return
        try:
            WDA_EXCLUDEFROMCAPTURE = 0x00000011
            hwnd = ctypes.c_void_p(int(self.winId()))
            user32 = ctypes.WinDLL("user32")
            user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        except (OSError, AttributeError, ValueError):
            pass

    def _apply_noactivate(self) -> None:
        """WA_ShowWithoutActivating + NoFocus block keyboard FOCUS, not OS
        foreground ACTIVATION - confirmed live (2026-09-19) that a click on
        the window was still activating it, which pauses the game
        underneath. WS_EX_NOACTIVATE is the real fix; there's no Qt-level
        attribute for it. One-time, same silent-failure posture as the other
        calls here - never raise or leave a broken window."""
        if sys.platform != "win32":
            return
        try:
            hwnd = ctypes.c_void_p(int(self.winId()))
            current = _get_ex_style(hwnd)
            _set_ex_style(hwnd, current | _WS_EX_NOACTIVATE)
        except (OSError, AttributeError, ValueError):
            pass

    def _apply_corner_rounding(self) -> None:
        """Best-effort native rounded corners via DwmSetWindowAttribute -
        mostly moot for a circular orb, kept for the rare platform/theme
        combination where the window's own square corners could otherwise
        peek out. Must never raise or leave a broken window - any failure
        (wrong Windows version, dwmapi missing) just leaves the window's
        own rectangular corners, which sit outside the painted circle."""
        if sys.platform != "win32":
            return
        try:
            hwnd = ctypes.c_void_p(int(self.winId()))
            dwmapi = ctypes.WinDLL("dwmapi")
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_ROUND = 2
            corner = ctypes.c_int(DWMWCP_ROUND)
            dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(corner), ctypes.sizeof(corner))
        except (OSError, AttributeError, ValueError):
            pass

    # ------------------------------------------------------------------ #
    # Painting
    # ------------------------------------------------------------------ #
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = self.SHADOW_MARGIN
        orb_footprint = ORB_OUTER_DIAMETER + 2 * m
        orb_left = (self.width() - orb_footprint) / 2  # window is wider than the orb, to fit the usage pill's text
        outer = QRectF(orb_left + m, m, ORB_OUTER_DIAMETER, ORB_OUTER_DIAMETER)
        key = _hud_key(self.state)
        draw_sphere(painter, outer.center(), ORB_SPHERE_RADIUS * breathing_scale(key, self._anim_phase),
                    state_color(key), glow=1.0)

        if self._click_through:
            # Click-through cue: a thin dashed ring shown ONLY while active -
            # not a permanent stroke (no border otherwise, by design).
            ring_color = QColor(self.theme.accent)
            ring_color.setAlphaF(0.55)
            painter.setPen(QPen(ring_color, 1.6, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(outer)

        self._paint_usage(painter, orb_footprint)
        painter.end()

    def _paint_usage(self, painter: QPainter, orb_footprint: float) -> None:
        """The remaining-calls-today pill, below the orb - see set_usage()."""
        if not self._usage_text:
            return
        font = painter.font()
        font.setPointSizeF(USAGE_FONT_POINT_SIZE)
        painter.setFont(font)
        text_w = QFontMetrics(font).horizontalAdvance(self._usage_text) + USAGE_PILL_PADDING
        text_w = min(text_w, self.width())  # defensive - the window is sized for USAGE_WIDTH_SAMPLE, not unbounded text
        pill = QRectF((self.width() - text_w) / 2, orb_footprint + USAGE_ROW_GAP, text_w, USAGE_ROW_HEIGHT)

        path = QPainterPath()
        path.addRoundedRect(pill, USAGE_ROW_HEIGHT / 2, USAGE_ROW_HEIGHT / 2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_parse_color(self.theme.surface_fill))
        painter.drawPath(path)

        painter.setPen(QColor(self.theme.text_secondary))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, self._usage_text)

    # ------------------------------------------------------------------ #
    # Mouse: left-drag to move. Nothing else (2026-09-29: right-click used
    # to switch to a light theme - removed, the dark look is the only one).
    # ------------------------------------------------------------------ #
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None

    # ------------------------------------------------------------------ #
    # Click-through hotkey polling
    # ------------------------------------------------------------------ #
    def _poll_click_through_hotkey(self) -> None:
        if self._hotkey_manager is None or not self._hotkey_manager.available:
            return
        if self._hotkey_manager.pressed():
            print(f"[overlay] {CLICK_THROUGH_HOTKEY!r} detectado (hotkey manager id={id(self._hotkey_manager)})")
            self._hotkey_manager.clear()
            self.set_click_through(not self._click_through)
