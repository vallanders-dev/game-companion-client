"""client/gui/ui_texts.py — the words in the desktop UI (main window, tray,
notices), in the player's language. The console's own texts live in
client/texts.py; these belong to the Qt process only."""
from __future__ import annotations

UI = {
    "pt": {
        "beta": "beta",
        "state_idle": "Pronto", "hint_idle": "Aperte {ask} e faça sua pergunta",
        "state_ouvindo": "Ouvindo…", "hint_ouvindo": "Pode falar, eu espero você terminar",
        "state_pensando": "Pensando…", "hint_pensando": "Buscando a resposta pra você",
        "state_pesquisando": "Pesquisando…", "hint_pesquisando": "Procurando na internet",
        "state_falando": "Falando…", "hint_falando": "Aperte {ask} para interromper",
        "state_pausado": "Pausado", "hint_pausado": "Retome pela bandeja do Windows",
        "conn_connecting": "Conectando…", "conn_ok": "Conectado",
        "conn_offline": "Sem conexão, tentando de novo…",
        "game": "JOGO", "game_none": "Nenhum jogo em foco", "game_none_sub": "Abra o jogo e ele aparece aqui",
        "game_sub": "Detectado automaticamente",
        "usage": "PERGUNTAS HOJE", "usage_left": "restantes de ~{cap}",
        "key_ask": "Perguntar", "key_note": "Anotar", "key_pad": "Controle",
        "voice": "VOZ", "settings": "Configurações",
        "footer": "Fechar esta janela não desliga o Ombri: ele continua na bandeja.",
        "wait_title": "Abra um jogo",
        "wait_body": "O Ombri reconhece a maioria dos jogos sozinho assim que eles estão na tela. "
                     "Jogo no navegador ou não reconhecido? Aperte {ask} e diga o nome do jogo.",
        "wait_tips": "ANTES DE JOGAR",
        "tip_window_b": "Janela sem bordas.",
        "tip_window": "Em tela cheia exclusiva o Windows pode esconder as teclas do Ombri.",
        "tip_admin_b": "Jogo como administrador?",
        "tip_admin": "Abra o Ombri como administrador também. Ele avisa se precisar.",
        "tip_mic_b": "Microfone pronto.", "tip_mic": "O Ombri usa o microfone padrão do Windows.",
        "tray_open": "Abrir Ombri", "tray_pause": "Pausar o Ombri", "tray_resume": "Retomar o Ombri",
        "tray_settings": "Configurações", "tray_quit": "Sair",
        "still_running": "O Ombri continua aqui na bandeja.",
        "admin_title": "Este jogo roda como administrador",
        "admin_body": "Para o {ask} e o {note} funcionarem dentro do jogo, o Ombri também precisa "
                      "rodar como administrador.",
        "admin_yes": "Reabrir como admin", "admin_no": "Agora não",
        "update_title": "Nova versão do Ombri",
        "update_body": "A versão {version} está pronta. Reinicie o Ombri pra atualizar: leva alguns segundos.",
        "update_yes": "Reiniciar", "update_no": "Depois",
        "announce_title": "Novidade do Ombri", "announce_ok": "Ok",
        "offline_title": "Sem conexão com o servidor",
        "offline_body": "O Ombri tenta de novo sozinho. Confira sua internet.",
    },
    "en": {
        "beta": "beta",
        "state_idle": "Ready", "hint_idle": "Press {ask} and ask your question",
        "state_ouvindo": "Listening…", "hint_ouvindo": "Go ahead, I'll wait until you finish",
        "state_pensando": "Thinking…", "hint_pensando": "Finding your answer",
        "state_pesquisando": "Searching…", "hint_pesquisando": "Looking it up online",
        "state_falando": "Speaking…", "hint_falando": "Press {ask} to interrupt",
        "state_pausado": "Paused", "hint_pausado": "Resume from the Windows tray",
        "conn_connecting": "Connecting…", "conn_ok": "Connected",
        "conn_offline": "No connection, retrying…",
        "game": "GAME", "game_none": "No game in focus", "game_none_sub": "Open your game and it shows up here",
        "game_sub": "Detected automatically",
        "usage": "QUESTIONS TODAY", "usage_left": "left of ~{cap}",
        "key_ask": "Ask", "key_note": "Note", "key_pad": "Controller",
        "voice": "VOICE", "settings": "Settings",
        "footer": "Closing this window doesn't stop Ombri: it keeps running in the tray.",
        "wait_title": "Open a game",
        "wait_body": "Ombri recognizes most games on its own as soon as they're on screen. "
                     "Browser game, or not recognized? Press {ask} and say the game's name.",
        "wait_tips": "BEFORE YOU PLAY",
        "tip_window_b": "Borderless window.",
        "tip_window": "In exclusive fullscreen Windows may hide Ombri's keys.",
        "tip_admin_b": "Game runs as administrator?",
        "tip_admin": "Run Ombri as administrator too. It tells you when it's needed.",
        "tip_mic_b": "Microphone ready.", "tip_mic": "Ombri uses Windows' default microphone.",
        "tray_open": "Open Ombri", "tray_pause": "Pause Ombri", "tray_resume": "Resume Ombri",
        "tray_settings": "Settings", "tray_quit": "Quit",
        "still_running": "Ombri is still here in the tray.",
        "admin_title": "This game runs as administrator",
        "admin_body": "For {ask} and {note} to work inside the game, Ombri needs to run as administrator too.",
        "admin_yes": "Reopen as admin", "admin_no": "Not now",
        "update_title": "New Ombri version",
        "update_body": "Version {version} is ready. Restart Ombri to update: it takes a few seconds.",
        "update_yes": "Restart", "update_no": "Later",
        "announce_title": "Ombri news", "announce_ok": "OK",
        "offline_title": "No connection to the server",
        "offline_body": "Ombri keeps retrying on its own. Check your internet.",
    },
}


# The server counts USES (a paid call each), not questions: a question costs
# about 4 (speech-to-text, the screen read, the answer, the voice) and 5 when
# Ombri searches the web. Players think in questions, so the app shows an
# estimate - "~38 restantes de ~50" for 48 uses used of 200 - never the raw
# use count labeled as questions (what it did until v0.1.11).
USES_PER_QUESTION = 4


def questions(uses: int) -> int:
    return max(0, uses) // USES_PER_QUESTION


def ui(lang: str, key: str, **kw) -> str:
    table = UI.get(lang, UI["pt"])
    text = table.get(key, UI["pt"][key])
    return text.format(**kw) if kw else text
