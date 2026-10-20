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


VOICES: tuple[Voice, ...] = (
    Voice("raquel", "pt", "Raquel", "Raquel (feminina)", "Raquel (female)"),
    Voice("yuri", "pt", "Yuri", "Yuri (masculina)", "Yuri (male)"),
    Voice("lily", "en", "Lily", "Lily (feminina, britânica)", "Lily (female, British)"),
    Voice("ivanna", "en", "Ivanna", "Ivanna (feminina, americana)", "Ivanna (female, American)"),
    Voice("adam", "en", "Adam", "Adam (masculina, americana)", "Adam (male, American)"),
)
BY_KEY = {v.key: v for v in VOICES}
DEFAULT_VOICE = "raquel"
LANGUAGES = {"pt": "Português (Brasil)", "en": "English"}
DEFAULT_VOICE_FOR = {"pt": "raquel", "en": "adam"}
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
