"""client/fixed_lines.py — every fixed-text line the client speaks, as data.

Pure constants, no imports beyond pathlib: `server/tools/bake_fixed_audio.py`
reads these texts to synthesize the shipped WAVs, and `client/fixed_audio.py`
plays them back. This process has no ElevenLabs key, so a line that isn't
baked into an asset can only be printed, never spoken.

Since 2026-09-27 every line exists per LANGUAGE (pt / en) and is baked per
VOICE (client/voices.py): `client/assets/voices/<voice>/<line>.wav`, with a
`manifest.json` per voice folder recording the text each WAV was baked
from (a changed text re-bakes on the next run).

The no-notes and closing lines are baked too, but their TEXT lives
server-side (`server/languages.py` - the server sends it as the turn's
answer_text); the bake tool pulls it from there so the audio can't drift
from what the server says. The no-notes line names the remember hotkey, so
it's baked for NO_NOTES_BAKED_HOTKEY only - a rebound F6 prints the line
without audio rather than speaking the wrong key.
"""
from __future__ import annotations

from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
VOICE_ASSETS_DIR = ASSETS_DIR / "voices"
MANIFEST_NAME = "manifest.json"

# Short on purpose (see client/fixed_audio.py's FillerPlayer): a filler that
# started plays to completion and the answer queues behind it.
FILLER_LINES = {
    "pt": ("Deixa eu ver...", "Só um instante...", "Verificando...", "Hmm, deixa eu olhar aqui..."),
    "en": ("Let me see...", "One sec...", "Checking...", "Hmm, let me look..."),
}

# A web search takes 3-10 s before a word exists: "web_search" plays as soon
# as the server says it's searching, "web_search_still" only if the answer
# still hasn't started WEB_SEARCH_STILL_AFTER seconds later.
WEB_SEARCH_STILL_AFTER = 6.0

TEXTS: dict[str, dict[str, str]] = {
    "pt": {
        "cap_reached": "O limite diário de chamadas do teste foi atingido. "
                       "Amanhã ele reseta sozinho; até lá eu não consigo responder.",
        "game_ask": "Não identifiquei o jogo automaticamente. Qual jogo você está jogando?",
        "game_reask": "Não reconheci. Qual jogo você está jogando?",
        "game_unresolved": "Não consegui identificar o jogo por voz. Aperte F8 ou F6 pra tentar de novo.",
        "note_saved": "Anotado.",
        # Spoken once per detected game when the game runs as administrator
        # and the client doesn't - Windows then hides every key press.
        "game_needs_admin": "Atenção: o jogo está rodando como administrador, então o Windows não me deixa "
                            "ouvir as teclas. Feche o Parça e abra de novo com o botão direito no ícone, em "
                            "Executar como administrador.",
        "web_search": "Deixa eu pesquisar isso rapidinho.",
        "web_search_still": "Tô quase lá, só mais um instante.",
        # A second copy of the client (both would hear every key press and
        # answer twice - seen with the first tester).
        "already_running": "O Parça já está aberto em outra janela. Feche a outra antes de abrir de novo.",
        # Played by the settings window's "Ouvir" button.
        "sample": "Oi! Eu sou o Parça. É só apertar F8 e me perguntar o que quiser sobre o seu jogo.",
    },
    "en": {
        "cap_reached": "The test's daily call limit has been reached. "
                       "It resets by itself tomorrow; until then I can't answer.",
        "game_ask": "I couldn't detect your game automatically. Which game are you playing?",
        "game_reask": "I didn't recognize that. Which game are you playing?",
        "game_unresolved": "I couldn't identify the game by voice. Press F8 or F6 to try again.",
        "note_saved": "Noted.",
        "game_needs_admin": "Heads up: your game is running as administrator, so Windows won't let me hear "
                            "your keys. Close Parça and open it again by right-clicking the icon and "
                            "choosing Run as administrator.",
        "web_search": "Let me look that up real quick.",
        "web_search_still": "Almost there, just a moment.",
        "already_running": "Parça is already open in another window. Close that one before opening it again.",
        "sample": "Hey! I'm Parça. Just press F8 and ask me anything about your game.",
    },
}

NO_NOTES_LINE = "no_notes"
NO_NOTES_BAKED_HOTKEY = "f6"
CLOSING_LINE = "closing_ack"

# Kept for the Portuguese-only callers and the console text of older paths.
CAP_REACHED_MESSAGE = TEXTS["pt"]["cap_reached"]
GAME_ASK_MESSAGE = TEXTS["pt"]["game_ask"]
GAME_ASK_REASK_MESSAGE = TEXTS["pt"]["game_reask"]
GAME_UNRESOLVED_MESSAGE = TEXTS["pt"]["game_unresolved"]
NOTE_SAVED_MESSAGE = TEXTS["pt"]["note_saved"]
GAME_NEEDS_ADMIN_MESSAGE = TEXTS["pt"]["game_needs_admin"]
WEB_SEARCH_MESSAGE = TEXTS["pt"]["web_search"]
WEB_SEARCH_STILL_MESSAGE = TEXTS["pt"]["web_search_still"]
ALREADY_RUNNING_MESSAGE = TEXTS["pt"]["already_running"]


def text(name: str, language: str) -> str:
    return TEXTS.get(language, TEXTS["pt"]).get(name, TEXTS["pt"].get(name, ""))


def lines_for(language: str) -> dict[str, str]:
    """asset name -> text for one language. The no-notes and closing lines
    are added by the bake tool (see above)."""
    fillers = FILLER_LINES.get(language, FILLER_LINES["pt"])
    return {**{f"filler_{n}": t for n, t in enumerate(fillers, 1)}, **TEXTS.get(language, TEXTS["pt"])}


def voice_dir(voice: str) -> Path:
    return VOICE_ASSETS_DIR / voice


def asset_path(name: str, voice: str = "raquel") -> Path:
    return voice_dir(voice) / f"{name}.wav"
