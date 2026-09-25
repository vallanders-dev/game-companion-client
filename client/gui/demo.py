"""gui/demo.py — python -m gui.demo

Cycles the HUD through idle -> ouvindo -> pensando -> falando -> idle with
fake transcript/answer text, a fake detected game, and a fake usage-cap
number. No mic, no Kimi, no ElevenLabs, no Chroma — this exists so the
overlay's look and window behavior can be judged over a real game without
touching any paid API or the real voice loop (which this module never
imports).
"""

from __future__ import annotations

import itertools
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from client.gui.global_keys import AsyncKeyPoller
from client.gui.overlay import CLICK_THROUGH_HOTKEY, DARK_THEME, HudState, OverlayWindow

FAKE_GAME = "The Witcher 3"
FAKE_CAP_TOTAL = 100
FAKE_TURNS = [
    (
        "Onde fica a ferraria mais próxima em Vizima?",
        "Tem uma ferraria perto da praça central, na parte comercial — "
        "siga a rua principal a partir da estátua.",
    ),
    (
        "Como eu derroto o grifo mais rápido?",
        "Usa óleo de grifo na espada e o sinal de Quen antes do combate; "
        "ataca os flancos quando ele pousar pra recuperar o fôlego.",
    ),
    (
        "Qual é o preço médio de uma espada de aço em Novigrado?",
        "Costuma variar entre 150 e 300 coroas, dependendo da qualidade e "
        "de quem tá vendendo.",
    ),
]

CYCLE_INTERVAL_MS = 2600
AUTO_STATE_ORDER = [HudState.IDLE, HudState.LISTENING, HudState.THINKING, HudState.SPEAKING]

# Manual-force keys for fast visual iteration. Global keys via the same
# gui.global_keys.AsyncKeyPoller overlay.py uses for its own click-through
# toggle, so they work even while a game window has focus (this HUD
# never does).
MANUAL_BINDINGS = {
    "f1": HudState.IDLE,
    "f2": HudState.LISTENING,
    "f3": HudState.THINKING,
    "f4": HudState.SPEAKING,
}
RESUME_AUTO_KEY = "f5"

# Presence-tier overrides (2026-09 restyle). F1-F6 and F8 are already
# spoken for (state-force keys above, main.py's real ask/remember
# triggers); overlay.CLICK_THROUGH_HOTKEY (Scroll Lock by default, not a
# function key at all since 2026-09-19) is imported rather than
# hardcoded here for the same reason - F10/F11/F12 are the next plain
# function keys down the row, unused and uncontested.
PRESENCE_BINDINGS = {
    "f10": "ativo",
    "f11": "repouso",
    "f12": "ausente",
}


class DemoController:
    def __init__(self, window: OverlayWindow) -> None:
        self.window = window
        self.auto = True
        self._turns = itertools.cycle(FAKE_TURNS)
        self._states = itertools.cycle(AUTO_STATE_ORDER)
        self._calls_made = 12
        self._current_question = ""
        self._current_answer = ""

        self.window.set_game(FAKE_GAME)
        self.window.set_usage(self._calls_made, FAKE_CAP_TOTAL)

        self._timer = QTimer()
        self._timer.timeout.connect(self._auto_tick)
        self._timer.start(CYCLE_INTERVAL_MS)

    def _auto_tick(self) -> None:
        if self.auto:
            self.force(next(self._states), _resume_auto_after=False)

    def force(self, state: HudState, _resume_auto_after: bool = True) -> None:
        if _resume_auto_after:
            self.auto = False
        if state == HudState.LISTENING:
            self._current_question, self._current_answer = next(self._turns)
            self.window.set_state(state, question=self._current_question, answer="")
        elif state == HudState.THINKING:
            self.window.set_state(state, question=self._current_question, answer="")
        elif state == HudState.SPEAKING:
            self._calls_made += 1
            self.window.set_usage(self._calls_made, FAKE_CAP_TOTAL)
            self.window.set_state(state, question=self._current_question, answer=self._current_answer)
        else:
            self.window.set_state(state)

    def resume_auto(self) -> None:
        self.auto = True


def _bind_manual_keys(controller: DemoController, window: OverlayWindow) -> list[tuple[AsyncKeyPoller, callable]]:
    bindings: list[tuple[AsyncKeyPoller, callable]] = []
    for key, state in MANUAL_BINDINGS.items():
        bindings.append((AsyncKeyPoller(key), lambda s=state: controller.force(s)))
    bindings.append((AsyncKeyPoller(RESUME_AUTO_KEY), controller.resume_auto))
    for key, tier in PRESENCE_BINDINGS.items():
        bindings.append((AsyncKeyPoller(key), lambda t=tier: window.debug_set_presence_tier(t)))
    return bindings


def main() -> None:
    app = QApplication(sys.argv)
    window = OverlayWindow(theme=DARK_THEME)
    window.show()

    controller = DemoController(window)
    manual_bindings = _bind_manual_keys(controller, window)

    poll = QTimer()

    def _poll_manual() -> None:
        for manager, callback in manual_bindings:
            if manager.available and manager.pressed():
                manager.clear()
                callback()

    poll.timeout.connect(_poll_manual)
    poll.start(50)

    print("Demo do HUD rodando (python -m gui.demo).")
    print("  F1 idle (orbe) · F2 ouvindo · F3 pensando · F4 falando · F5 volta ao ciclo automático")
    print("  F10/F11/F12 força a presença ativo/repouso/ausente (aperte F1 antes — só afeta o orbe)")
    print(f"  {CLICK_THROUGH_HOTKEY!r} alterna clique-atravessa (a janela ignora o mouse)")
    print("  clique-direito no orbe alterna tema claro/escuro")
    print("  clique-esquerdo e arraste move a janela")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
