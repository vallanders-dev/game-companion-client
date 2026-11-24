"""client/texts.py — the console text a tester reads, per language (2026-09-27).

The spoken lines live in client/fixed_lines.py; this is only what the
console prints. Developer telemetry (`client.main.telemetry()`) stays in
Portuguese on purpose - it's read by the developer, not the tester.
`t()` falls back to Portuguese for a key missing in English or Spanish.
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
        "game_picked": "  Jogo (escolhido na janela): {name}",
        "retry_prompt": "Aperte F8 ou F6 pra tentar de novo: ",
        "scene_prompt": "Cena — {ask} pra perguntar por voz, {note} pra anotar algo, digite a cena, "
                        "ou digite 'jogo' pra escolher o jogo manualmente: ",
        "interrupted_note": "  (interrompido — anotar)",
        "interrupted_ask": "  (interrompido — nova pergunta)",
        "follow_up_heard": "  (continuação, sem tecla)",
        "wake_heard": "  (Ei, Ombri — ouvido)",
        "stopped_by_voice": "  (parei: você disse chega)",
        "guide_word_heard": "  (guia: \"{word}\" — ouvido aqui no PC)",
        "game_prompt": "Jogo: ",
        "bye": "Até a próxima!",
        "paused": "Ombri pausado: F8/F6 não fazem nada até você retomar pela bandeja.",
        "resumed": "Ombri de volta: F8/F6 funcionando.",
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
        "answer": "  Ombri: {text}",
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
        "recap_offer": "  '{game}' fechou: perguntando se guardo onde você parou...",
        "recap_nothing": "  (nada pra guardar desta sessão)",
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
        "game_picked": "  Game (picked in the window): {name}",
        "retry_prompt": "Press F8 or F6 to try again: ",
        "scene_prompt": "Scene — {ask} to ask by voice, {note} to save a note, type the scene, "
                        "or type 'jogo' to pick the game manually: ",
        "interrupted_note": "  (interrupted — saving a note)",
        "interrupted_ask": "  (interrupted — new question)",
        "follow_up_heard": "  (follow-up, no key)",
        "wake_heard": "  (Hey Ombri — heard)",
        "stopped_by_voice": "  (stopped: you said stop)",
        "guide_word_heard": "  (guide: \"{word}\" — heard here on the PC)",
        "game_prompt": "Game: ",
        "bye": "See you next time!",
        "paused": "Ombri paused: F8/F6 do nothing until you resume it from the tray.",
        "resumed": "Ombri is back: F8/F6 working.",
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
        "answer": "  Ombri: {text}",
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
        "recap_offer": "  '{game}' closed: asking whether to keep where you stopped...",
        "recap_nothing": "  (nothing to keep from this session)",
    },
    # Spanish: neutral Latin American "tú" (docs/spanish.md §5). The hotkey
    # names (F8/F6) and the typed command 'jogo' are the same in every
    # language - they are what the program reads, not what it says.
    "es": {
        "connecting": "Conectando a {url}...",
        "connected": "Conectado como '{name}'.",
        "no_token_bye": "Sin token de tester - ¡hasta la próxima!",
        "token_rejected": "ERROR: el servidor no aceptó el token de tester.",
        "server_down": "  (servidor caído o sin internet - inténtalo de nuevo en un momento)",
        "paste_token": "Pega tu token de tester: ",
        "hotkey_ask": "Hotkey: {key}  ->  captura la pantalla + graba tu pregunta hablada",
        "hotkey_note": "Hotkey: {key}  ->  guarda una nota personal (no responde)",
        "gamepad": "Control: mantén {combo} — lo mismo que el hotkey de pregunta",
        "tip": "  Consejo: corre el juego en 'ventana sin bordes' / 'pantalla completa en ventana'. En\n"
               "  pantalla completa exclusiva el hotkey global puede no detectarse.",
        "game_detected": "  Juego detectado: {name}",
        "attention": "  ATENCIÓN: {text}",
        "game_by_voice_free": "  Juego (sin contenido curado): {name}",
        "game_by_voice": "  Juego (confirmado por voz): {name}",
        "game_picked": "  Juego (elegido en la ventana): {name}",
        "retry_prompt": "Presiona F8 o F6 para intentarlo de nuevo: ",
        "scene_prompt": "Escena — {ask} para preguntar por voz, {note} para anotar algo, escribe la escena, "
                        "o escribe 'jogo' para elegir el juego a mano: ",
        "interrupted_note": "  (interrumpido — anotar)",
        "interrupted_ask": "  (interrumpido — nueva pregunta)",
        "follow_up_heard": "  (continuación, sin tecla)",
        "wake_heard": "  (Oye, Ombri — escuchado)",
        "stopped_by_voice": "  (paré: dijiste basta)",
        "guide_word_heard": "  (guía: \"{word}\" — escuchado aquí en el PC)",
        "game_prompt": "Juego: ",
        "bye": "¡Hasta la próxima!",
        "paused": "Ombri en pausa: F8/F6 no hacen nada hasta que lo reanudes desde la bandeja.",
        "resumed": "Ombri de vuelta: F8/F6 funcionando.",
        "capturing": "  Capturando la pantalla...",
        "capture_failed": "  (no pude capturar la pantalla — sigo sin ella)",
        "speak_question": "  Haz tu pregunta (se detiene solo tras {hang:.1f}s de silencio)...",
        "no_voice_type": "  (sin pregunta por voz — escríbela abajo)",
        "question_prompt": "Pregunta: ",
        "empty_question": "  (pregunta vacía — inténtalo de nuevo)\n",
        "no_audio_out": "  (sin salida de audio: {error})",
        "you_asked": '  Preguntaste: "{question}"',
        "scene_detected": "  Escena detectada: {scene}",
        "error": "  ERROR: {error}",
        "answer": "  Ombri: {text}",
        "no_quota_voice": "  (sin cuota para decir la respuesta — queda en el texto de arriba)",
        "voice_failed": "  (la voz falló en esta respuesta: {error})",
        "noting": "  Guardando una nota personal para '{game}'...",
        "speak_note": "  Di lo que quieres anotar (se detiene solo tras {hang:.1f}s de silencio)...",
        "note_not_heard_retry": "  (no entendí la nota — no se guardó nada; presiona F6 para intentarlo de nuevo)",
        "note_not_heard": "  (no entendí la nota — no se guardó nada)",
        "answer_yes_no": "  Responde por voz: sí o no...",
        "note_saved": '  Anotado: "{text}"',
        "note_discarded": "  Nota descartada ({reason}).",
        "note_cap": "  {text} (nota no guardada)",
        "voice_changed": "  Voz ahora: {name}.",
        "recap_offer": "  '{game}' se cerró: preguntando si guardo dónde te quedaste...",
        "recap_nothing": "  (nada que guardar de esta sesión)",
    },
}


def set_language(language: str) -> None:
    global _current
    _current = language if language in MESSAGES else "pt"


def language() -> str:
    return _current


def t(key: str, /, **kw) -> str:  # positional-only: templates use {key} too (hotkey_ask)
    template = MESSAGES[_current].get(key) or MESSAGES["pt"].get(key, key)
    return template.format(**kw) if kw else template


def spoken(name: str) -> str:
    """The text of a spoken fixed line, in the current language (for printing)."""
    return spoken_text(name, _current)
