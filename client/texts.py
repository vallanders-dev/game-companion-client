"""client/texts.py — the console text a tester reads, per language (2026-09-27).

The spoken lines live in client/fixed_lines.py; this is only what the
console prints. Developer telemetry (`client.main.telemetry()`) stays in
Portuguese on purpose - it's read by the developer, not the tester.
`t()` falls back to Portuguese for a key missing in English.
"""
from __future__ import annotations

from client.fixed_lines import text as spoken_text

_current = "pt"

MESSAGES: dict[str, dict[str, str]] = {
    "pt": {
        "connecting": "Conectando a {url}...",
        "connected": "Conectado como '{name}'.",
        "no_token_bye": "Sem token de testador - até a próxima!",
        "token_rejected": "ERRO: o servidor não aceitou o token de testador.",
        "server_down": "  (servidor fora do ar ou sem internet - tente de novo em instantes)",
        "paste_token": "Cole o seu token de testador: ",
        "hotkey_ask": "Hotkey: {key}  ->  screenshot + grava a pergunta falada",
        "hotkey_note": "Hotkey: {key}  ->  anota uma nota pessoal (não responde)",
        "gamepad": "Controle: segure {combo} — mesma ação do hotkey de pergunta",
        "tip": "  Dica: rode o jogo em 'janela sem bordas' / 'fullscreen em janela'. Em\n"
               "  fullscreen exclusivo o hotkey global pode não ser detectado.",
        "game_detected": "  Jogo detectado: {name}",
        "attention": "  ATENÇÃO: {text}",
        "game_by_voice_free": "  Jogo (sem conteúdo curado): {name}",
        "game_by_voice": "  Jogo (confirmado por voz): {name}",
        "retry_prompt": "Aperte F8 ou F6 pra tentar de novo: ",
        "scene_prompt": "Cena — {ask} pra perguntar por voz, {note} pra anotar algo, digite a cena, "
                        "ou digite 'jogo' pra escolher o jogo manualmente: ",
        "interrupted_note": "  (interrompido — anotar)",
        "interrupted_ask": "  (interrompido — nova pergunta)",
        "game_prompt": "Jogo: ",
        "bye": "Até a próxima!",
        "capturing": "  Capturando a tela...",
        "capture_failed": "  (não consegui capturar a tela — seguindo sem ela)",
        "speak_question": "  Fale a sua pergunta (para sozinho após {hang:.1f}s de silêncio)...",
        "no_voice_type": "  (sem pergunta por voz — digite abaixo)",
        "question_prompt": "Pergunta: ",
        "empty_question": "  (pergunta vazia — tente de novo)\n",
        "no_audio_out": "  (sem saída de áudio: {error})",
        "you_asked": '  Você perguntou: "{question}"',
        "scene_detected": "  Cena detectada: {scene}",
        "error": "  ERRO: {error}",
        "answer": "  Parça: {text}",
        "no_quota_voice": "  (sem cota pra falar a resposta — ela fica no texto acima)",
        "voice_failed": "  (voz falhou nessa resposta: {error})",
        "noting": "  Anotando uma nota pessoal para '{game}'...",
        "speak_note": "  Fale o que quer anotar (para sozinho após {hang:.1f}s de silêncio)...",
        "note_not_heard_retry": "  (não entendi a nota — nada foi salvo; aperte F6 pra tentar de novo)",
        "note_not_heard": "  (não entendi a nota — nada foi salvo)",
        "answer_yes_no": "  Responda por voz: sim ou não...",
        "note_saved": '  Anotado: "{text}"',
        "note_discarded": "  Nota descartada ({reason}).",
        "note_cap": "  {text} (nota não salva)",
        "voice_changed": "  Voz agora: {name}.",
    },
    "en": {
        "connecting": "Connecting to {url}...",
        "connected": "Connected as '{name}'.",
        "no_token_bye": "No tester token - see you next time!",
        "token_rejected": "ERROR: the server did not accept the tester token.",
        "server_down": "  (server down or no internet - try again in a moment)",
        "paste_token": "Paste your tester token: ",
        "hotkey_ask": "Hotkey: {key}  ->  screenshot + records your spoken question",
        "hotkey_note": "Hotkey: {key}  ->  saves a personal note (no answer)",
        "gamepad": "Controller: hold {combo} — same as the question hotkey",
        "tip": "  Tip: run your game in 'borderless window' / 'windowed fullscreen'. In\n"
               "  exclusive fullscreen the global hotkey may not be detected.",
        "game_detected": "  Game detected: {name}",
        "attention": "  HEADS UP: {text}",
        "game_by_voice_free": "  Game (no curated content): {name}",
        "game_by_voice": "  Game (confirmed by voice): {name}",
        "retry_prompt": "Press F8 or F6 to try again: ",
        "scene_prompt": "Scene — {ask} to ask by voice, {note} to save a note, type the scene, "
                        "or type 'jogo' to pick the game manually: ",
        "interrupted_note": "  (interrupted — saving a note)",
        "interrupted_ask": "  (interrupted — new question)",
        "game_prompt": "Game: ",
        "bye": "See you next time!",
        "capturing": "  Capturing the screen...",
        "capture_failed": "  (couldn't capture the screen — going on without it)",
        "speak_question": "  Ask your question (stops by itself after {hang:.1f}s of silence)...",
        "no_voice_type": "  (no spoken question — type it below)",
        "question_prompt": "Question: ",
        "empty_question": "  (empty question — try again)\n",
        "no_audio_out": "  (no audio output: {error})",
        "you_asked": '  You asked: "{question}"',
        "scene_detected": "  Scene detected: {scene}",
        "error": "  ERROR: {error}",
        "answer": "  Parça: {text}",
        "no_quota_voice": "  (no quota left to speak the answer — it's in the text above)",
        "voice_failed": "  (the voice failed for this answer: {error})",
        "noting": "  Saving a personal note for '{game}'...",
        "speak_note": "  Say what you want to save (stops by itself after {hang:.1f}s of silence)...",
        "note_not_heard_retry": "  (didn't catch the note — nothing was saved; press F6 to try again)",
        "note_not_heard": "  (didn't catch the note — nothing was saved)",
        "answer_yes_no": "  Answer out loud: yes or no...",
        "note_saved": '  Noted: "{text}"',
        "note_discarded": "  Note discarded ({reason}).",
        "note_cap": "  {text} (note not saved)",
        "voice_changed": "  Voice is now: {name}.",
    },
}


def set_language(language: str) -> None:
    global _current
    _current = language if language in MESSAGES else "pt"


def language() -> str:
    return _current


def t(key: str, **kw) -> str:
    template = MESSAGES[_current].get(key) or MESSAGES["pt"].get(key, key)
    return template.format(**kw) if kw else template


def spoken(name: str) -> str:
    """The text of a spoken fixed line, in the current language (for printing)."""
    return spoken_text(name, _current)
