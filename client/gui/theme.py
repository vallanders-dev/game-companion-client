"""gui/theme.py — palette definitions for the HUD (see overlay.py).

Plain dataclasses of QSS/CSS-style color strings (hex or `rgba(r, g, b, a)`)
so overlay.py never hardcodes a color; overlay.py's `_parse_color()` turns
either form into a QColor at paint time. Accent is a warm coral, chosen to
not read as a copy of Discord's blurple (#5865F2) or Steam's blue
(#1b2838 / #66c0f4) — kept identical across both palettes for brand
consistency (same idea as macOS Control Center reusing one accent
regardless of light/dark). State colors are gray/green/amber/coral, one
per HUD sub-state; overlay.py pairs each with a distinct icon shape too
(ring/circle/diamond/bars), since color alone isn't enough (colorblind
accessibility) — see overlay.py's StateIndicator.

2026-09 restyle: dark-theme values below are the specified design tokens,
translated as directly as a QPainter/QSS world allows. Light theme keeps
the same structural tokens (surface/text tiers, same accent) but its
exact values are an adaptation, not independently calibrated — dark is
the theme that matters most here, per this session's brief.

2026-09-25: `border_hairline` removed - live feedback ("I don't want that
[card outline]... it's not cute") took the card's stroke out entirely;
depth now comes from the drop shadow / native Mica-Acrylic backdrop alone
(see overlay.py's `_paint_card()`).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StateColors:
    idle: str        # "ocioso" - ring shape (only the orb renders while idle; kept for API completeness)
    listening: str    # "ouvindo" - filled circle
    thinking: str     # "pensando" - diamond
    speaking: str     # "falando" - bars (== accent, see overlay.py's draw_bars/logo mark)


@dataclass(frozen=True)
class Theme:
    name: str
    text_primary: str
    text_secondary: str
    text_muted: str
    accent: str
    surface_fill: str        # card background
    orb_core_fill: str       # orb's inset inner core
    divider: str             # 1px rule between game name and captions
    inner_highlight: str     # 1px inset top highlight (glass bevel)
    corner_radius: int       # card corner radius, px
    states: StateColors


DARK_THEME = Theme(
    name="dark",
    text_primary="#E7E9ED",
    text_secondary="#9AA0AC",
    text_muted="#6B7280",
    accent="#FF6B4A",
    surface_fill="rgba(20, 22, 28, 0.80)",
    orb_core_fill="rgba(24, 27, 34, 0.62)",
    divider="rgba(255, 255, 255, 0.07)",
    inner_highlight="rgba(255, 255, 255, 0.06)",
    corner_radius=16,
    states=StateColors(
        idle="#6B7280",
        listening="#34D399",
        thinking="#F5B740",
        speaking="#FF6B4A",
    ),
)

LIGHT_THEME = Theme(
    name="light",
    text_primary="#1C1D21",
    text_secondary="#54565F",
    text_muted="#8A8D99",
    accent="#FF6B4A",
    surface_fill="rgba(255, 255, 255, 0.80)",
    orb_core_fill="rgba(255, 255, 255, 0.62)",
    divider="rgba(0, 0, 0, 0.06)",
    inner_highlight="rgba(255, 255, 255, 0.50)",
    corner_radius=16,
    states=StateColors(
        idle="#9AA0A6",
        listening="#22A159",
        thinking="#C97F0F",
        speaking="#E85A3B",
    ),
)
