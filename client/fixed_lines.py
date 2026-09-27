"""client/fixed_lines.py — every fixed-text line the client speaks, as data.

Pure constants, no imports beyond pathlib: `server/tools/bake_fixed_audio.py`
reads these texts to synthesize the shipped WAVs in `client/assets/`, and
`client/fixed_audio.py` plays them back. This process has no ElevenLabs key,
so a line that isn't baked into an asset can only be printed, never spoken.

The no-notes line is baked too, but its TEXT lives server-side
(`server/companion.py`'s NO_NOTES_ANSWER - the server sends it as the turn's
answer_text); the bake tool pulls it from there so the audio can't drift
from what the server says. It names the remember hotkey, so it's baked for
NO_NOTES_BAKED_HOTKEY only - a rebound F6 prints the line without audio
rather than speaking the wrong key.
"""
from __future__ import annotations

from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
MANIFEST_NAME = "manifest.json"

# Short on purpose (see client/fixed_audio.py's FillerPlayer): a filler that
# started plays to completion and the answer queues behind it.
FILLER_LINES = (
    "Deixa eu ver...",
    "Só um instante...",
    "Verificando...",
    "Hmm, deixa eu olhar aqui...",
)

CAP_REACHED_MESSAGE = (
    "O limite diário de chamadas do teste foi atingido. "
    "Amanhã ele reseta sozinho; até lá eu não consigo responder."
)
GAME_ASK_MESSAGE = "Não identifiquei o jogo automaticamente. Qual jogo você está jogando?"
GAME_ASK_REASK_MESSAGE = "Não reconheci. Qual jogo você está jogando?"
GAME_UNRESOLVED_MESSAGE = "Não consegui identificar o jogo por voz. Aperte F8 ou F6 pra tentar de novo."
NOTE_SAVED_MESSAGE = "Anotado."
# A web search takes 3-10 s before a word exists: the first line plays as
# soon as the server says it's searching, the second only if the answer
# still hasn't started WEB_SEARCH_STILL_AFTER seconds later.
WEB_SEARCH_MESSAGE = "Deixa eu pesquisar isso rapidinho."
WEB_SEARCH_STILL_MESSAGE = "Tô quase lá, só mais um instante."
WEB_SEARCH_STILL_AFTER = 6.0
# Spoken when a second copy of the client is opened (both would hear every
# key press and answer twice - seen with the first tester).
ALREADY_RUNNING_MESSAGE = "O Parça já está aberto em outra janela. Feche a outra antes de abrir de novo."
# Spoken once per detected game when the game runs as administrator and the
# client doesn't - Windows then hides every key press from the client.
GAME_NEEDS_ADMIN_MESSAGE = (
    "Atenção: o jogo está rodando como administrador, então o Windows não me deixa ouvir as teclas. "
    "Feche o Parça e abra de novo com o botão direito no ícone, em Executar como administrador."
)

NO_NOTES_LINE = "no_notes"
NO_NOTES_BAKED_HOTKEY = "f6"
# The closing-phrase acknowledgment ("Beleza! ...") - text owned by the
# server (companion.CLOSING_ACK_MESSAGE, sent as answer_text), baked by
# the bake tool exactly like the no-notes line.
CLOSING_LINE = "closing_ack"

# asset name -> text. The no-notes and closing lines are added by the bake
# tool (see above).
LINES: dict[str, str] = {
    **{f"filler_{n}": text for n, text in enumerate(FILLER_LINES, 1)},
    "cap_reached": CAP_REACHED_MESSAGE,
    "game_ask": GAME_ASK_MESSAGE,
    "game_reask": GAME_ASK_REASK_MESSAGE,
    "game_unresolved": GAME_UNRESOLVED_MESSAGE,
    "note_saved": NOTE_SAVED_MESSAGE,
    "game_needs_admin": GAME_NEEDS_ADMIN_MESSAGE,
    "web_search": WEB_SEARCH_MESSAGE,
    "web_search_still": WEB_SEARCH_STILL_MESSAGE,
    "already_running": ALREADY_RUNNING_MESSAGE,
}


def asset_path(name: str) -> Path:
    return ASSETS_DIR / f"{name}.wav"
