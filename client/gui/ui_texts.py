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
        "usage": "PERGUNTAS HOJE", "usage_left": "restantes de {cap}",
        "key_ask": "Perguntar", "key_note": "Anotar", "key_pad": "Controle",
        "voice": "VOZ", "settings": "Configurações",
        "footer": "Fechar esta janela não desliga o Parça: ele continua na bandeja.",
        "wait_title": "Abra um jogo",
        "wait_body": "O Parça reconhece o jogo sozinho assim que ele estiver na tela. "
                     "Depois é só apertar {ask} e perguntar.",
        "wait_tips": "ANTES DE JOGAR",
        "tip_window_b": "Janela sem bordas.",
        "tip_window": "Em tela cheia exclusiva o Windows pode esconder as teclas do Parça.",
        "tip_admin_b": "Jogo como administrador?",
        "tip_admin": "Abra o Parça como administrador também. Ele avisa se precisar.",
        "tip_mic_b": "Microfone pronto.", "tip_mic": "O Parça usa o microfone padrão do Windows.",
        "tray_open": "Abrir Parça", "tray_pause": "Pausar o Parça", "tray_resume": "Retomar o Parça",
        "tray_settings": "Configurações", "tray_quit": "Sair",
        "still_running": "O Parça continua aqui na bandeja.",
        "admin_title": "Este jogo roda como administrador",
        "admin_body": "Para o {ask} e o {note} funcionarem dentro do jogo, o Parça também precisa "
                      "rodar como administrador.",
        "admin_yes": "Reabrir como admin", "admin_no": "Agora não",
        "offline_title": "Sem conexão com o servidor",
        "offline_body": "O Parça tenta de novo sozinho. Confira sua internet.",
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
        "usage": "QUESTIONS TODAY", "usage_left": "left of {cap}",
        "key_ask": "Ask", "key_note": "Note", "key_pad": "Controller",
        "voice": "VOICE", "settings": "Settings",
        "footer": "Closing this window doesn't stop Parça: it keeps running in the tray.",
        "wait_title": "Open a game",
        "wait_body": "Parça recognizes your game on its own as soon as it's on screen. "
                     "Then just press {ask} and ask.",
        "wait_tips": "BEFORE YOU PLAY",
        "tip_window_b": "Borderless window.",
        "tip_window": "In exclusive fullscreen Windows may hide Parça's keys.",
        "tip_admin_b": "Game runs as administrator?",
        "tip_admin": "Run Parça as administrator too. It tells you when it's needed.",
        "tip_mic_b": "Microphone ready.", "tip_mic": "Parça uses Windows' default microphone.",
        "tray_open": "Open Parça", "tray_pause": "Pause Parça", "tray_resume": "Resume Parça",
        "tray_settings": "Settings", "tray_quit": "Quit",
        "still_running": "Parça is still here in the tray.",
        "admin_title": "This game runs as administrator",
        "admin_body": "For {ask} and {note} to work inside the game, Parça needs to run as administrator too.",
        "admin_yes": "Reopen as admin", "admin_no": "Not now",
        "offline_title": "No connection to the server",
        "offline_body": "Parça keeps retrying on its own. Check your internet.",
    },
}


def ui(lang: str, key: str, **kw) -> str:
    table = UI.get(lang, UI["pt"])
    text = table.get(key, UI["pt"][key])
    return text.format(**kw) if kw else text
