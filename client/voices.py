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
    language: str  # "pt" | "en" | "es"
    name: str
    label_pt: str
    label_en: str
    label_es: str


# Visible names are stars (owner, 2026-10-27 / set chosen 2026-10-31), so they
# sound like Ombri's own voices; the pt/en keys stay the old ElevenLabs names
# for saved settings and the server. The Spanish pair (2026-11-03,
# docs/spanish.md §4.B/§4.C) is keyed by star name: no old name to keep.
VOICES: tuple[Voice, ...] = (
    Voice("yuri", "pt", "Rigel", "Rigel (calmo) · a voz do Ombri", "Rigel (calm) · Ombri's voice",
          "Rigel (calmo) · la voz de Ombri"),
    Voice("raquel", "pt", "Mira", "Mira (calorosa)", "Mira (warm)", "Mira (cálida)"),
    Voice("lily", "en", "Alya", "Alya (vibrante, britânica)", "Alya (bright, British)", "Alya (vibrante, británica)"),
    Voice("ivanna", "en", "Spica", "Spica (animada, americana)", "Spica (lively, American)",
          "Spica (animada, americana)"),
    Voice("adam", "en", "Altair", "Altair (firme, britânico) · a voz do Ombri", "Altair (steady, British) · Ombri's voice",
          "Altair (firme, británico) · la voz de Ombri"),
    # ElevenLabs professional voices: Vega es-AR, Sirio and Antares es-ES (the
    # owner, 2026-11-05: a less common accent gives Ombri personality). What
    # Ombri says is the neutral "tú" of server/languages.py.
    Voice("vega", "es", "Vega", "Vega (calorosa, acolhedora)", "Vega (warm, friendly)", "Vega (cálida, cercana)"),
    Voice("sirio", "es", "Sirio", "Sirio (simpático, Espanha)", "Sirio (friendly, Spain)", "Sirio (simpático, España)"),
    Voice("antares", "es", "Antares", "Antares (confiante, Espanha)", "Antares (confident, Spain)",
          "Antares (seguro, España)"),
)
BY_KEY = {v.key: v for v in VOICES}
# Rigel is Ombri's signature pt voice (owner, 2026-11-02: brand identity, "o
# Ombri"); a tester who already picked Mira keeps her. The server's fallback for
# a request with no voice stays Raquel (server/languages.py).
DEFAULT_VOICE = "yuri"
# Language names in their own language (the settings window's list).
LANGUAGES = {"pt": "Português (Brasil)", "en": "English", "es": "Español"}
# The short name the main window shows next to the voice ("Rigel · Português").
LANGUAGE_NAMES = {"pt": "Português", "en": "English", "es": "Español"}
# The first-launch voice per language. Spanish: Sirio (the owner has not named
# a Spanish signature voice - one line to change).
DEFAULT_VOICE_FOR = {"pt": "yuri", "en": "adam", "es": "sirio"}
# A voice that was replaced (2026-10-16: Hale -> Adam): a saved choice of the old one
# becomes the new one. Same table as server/languages.py VOICE_ALIASES.
ALIASES = {"hale": "adam"}


def get(key: str | None) -> Voice:
    k = (key or "").strip().lower()
    return BY_KEY.get(ALIASES.get(k, k)) or BY_KEY[DEFAULT_VOICE]


def for_language(language: str) -> list[Voice]:
    return [v for v in VOICES if v.language == language]


def label(voice: Voice, ui_language: str) -> str:
    return {"en": voice.label_en, "es": voice.label_es}.get(ui_language, voice.label_pt)


def language_name(language: str) -> str:
    return LANGUAGE_NAMES.get(language, LANGUAGE_NAMES["pt"])
