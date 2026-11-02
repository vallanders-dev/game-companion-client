"""client/voices.py — the voices a tester can pick (2026-09-27).

Only keys and labels live here: the server maps each key to the real
ElevenLabs voice (server/languages.py) and ignores anything it doesn't know.
A voice's language decides the whole conversation - speech recognition,
answers, fixed lines and the console text (client/texts.py).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Voice:
    key: str
    language: str  # "pt" | "en"
    name: str
    label_pt: str
    label_en: str


# Visible names are stars (owner, 2026-10-27 / set chosen 2026-10-31), so they
# sound like Ombri's own voices; the keys stay the old ElevenLabs names for
# saved settings and the server.
VOICES: tuple[Voice, ...] = (
    Voice("yuri", "pt", "Rigel", "Rigel (calmo)", "Rigel (calm)"),
    Voice("raquel", "pt", "Mira", "Mira (calorosa)", "Mira (warm)"),
    Voice("lily", "en", "Alya", "Alya (vibrante, britânica)", "Alya (bright, British)"),
    Voice("ivanna", "en", "Spica", "Spica (animada, americana)", "Spica (lively, American)"),
    Voice("adam", "en", "Altair", "Altair (firme, britânico)", "Altair (steady, British)"),
)
BY_KEY = {v.key: v for v in VOICES}
# Rigel is Ombri's signature pt voice (owner, 2026-11-02: brand identity, "o
# Ombri"); a tester who already picked Mira keeps her. The server's fallback for
# a request with no voice stays Raquel (server/languages.py).
DEFAULT_VOICE = "yuri"
LANGUAGES = {"pt": "Português (Brasil)", "en": "English"}
DEFAULT_VOICE_FOR = {"pt": "yuri", "en": "adam"}
# A voice that was replaced (2026-10-16: Hale -> Adam): a saved choice of the old one
# becomes the new one. Same table as server/languages.py VOICE_ALIASES.
ALIASES = {"hale": "adam"}


def get(key: str | None) -> Voice:
    k = (key or "").strip().lower()
    return BY_KEY.get(ALIASES.get(k, k)) or BY_KEY[DEFAULT_VOICE]


def for_language(language: str) -> list[Voice]:
    return [v for v in VOICES if v.language == language]


def label(voice: Voice, ui_language: str) -> str:
    return voice.label_en if ui_language == "en" else voice.label_pt
