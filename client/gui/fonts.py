"""gui/fonts.py — bundled Chakra Petch / Manrope, loaded via QFontDatabase.

Both are Google Fonts under the OFL (see gui/assets/fonts/*/OFL.txt).
Chakra Petch ships static weight files upstream; Manrope ships only as a
single variable font (`Manrope[wght].ttf`) in the same repo, so the
400/500/600 static instances here were generated once with
`fonttools varLib.instancer` (a build-time step, not a runtime
dependency — fonttools is not in requirements.txt) and committed as
plain static .ttf files like Chakra Petch's.

If any file fails to load (missing, corrupt, wrong path), heading_font()
and body_font() silently fall back to FALLBACK_FAMILY instead of leaving
a blank/broken font — verified by pointing FONT_FILES at nonexistent
paths and confirming QFontDatabase.addApplicationFont() returns -1 and
these two functions fall back correctly (see this session's report).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase

_FONT_DIR = Path(__file__).parent / "assets" / "fonts"

FONT_FILES = [
    _FONT_DIR / "ChakraPetch" / "ChakraPetch-SemiBold.ttf",
    _FONT_DIR / "ChakraPetch" / "ChakraPetch-Bold.ttf",
    _FONT_DIR / "Manrope" / "Manrope-Regular.ttf",
    _FONT_DIR / "Manrope" / "Manrope-Medium.ttf",
    _FONT_DIR / "Manrope" / "Manrope-SemiBold.ttf",
]

HEADING_FAMILY = "Chakra Petch"
BODY_FAMILY = "Manrope"
FALLBACK_FAMILY = "Segoe UI"

_loaded = False
_load_ok = False


def ensure_fonts_loaded() -> bool:
    """Registers the bundled fonts with QFontDatabase. Idempotent, safe to
    call more than once (e.g. from both overlay.py and demo.py)."""
    global _loaded, _load_ok
    if _loaded:
        return _load_ok
    _loaded = True
    ok = True
    for path in FONT_FILES:
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            ok = False
    _load_ok = ok
    return ok


def heading_font(weight: QFont.Weight, size: float, tracking_percent: int | None = None) -> QFont:
    """Chakra Petch (state labels, section captions, uppercase text)."""
    family = HEADING_FAMILY if _load_ok else FALLBACK_FAMILY
    font = QFont(family)
    font.setPointSizeF(size)
    font.setWeight(weight)
    if tracking_percent is not None:
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, float(tracking_percent))
    return font


def body_font(weight: QFont.Weight, size: float, italic: bool = False) -> QFont:
    """Manrope (game name, question/answer captions)."""
    family = BODY_FAMILY if _load_ok else FALLBACK_FAMILY
    font = QFont(family)
    font.setPointSizeF(size)
    font.setWeight(weight)
    font.setItalic(italic)
    return font
