"""client/main.py — the real client loop, dispatching over the network.

The first genuinely real cut of the `client/` package the approved plan
describes: `main.py`'s trigger/game-detection loop shape is unchanged
(same `HotkeyManager`/`GamepadWatcher` racing, same `active_game`
persistence and miss-tolerance, same `prompt_scene()`/`GAME_CHANGED`
mid-wait game-switch detection - all of that is pure local hardware logic
and moved into `client/capture.py` completely unchanged), but every call
that used to go straight to `Companion`/`store`/`TtsStream` now goes
through `client/net.py`'s `ServerSession` instead - screenshot + WAV out,
streamed text/audio back.

**Scope of this first cut, deliberately bounded** (see CLAUDE.md for the
full reasoning): the F8 ask flow and F6 remember-note flow are both fully
wired end to end, including barge-in (local `AudioOut.interrupt()` first,
`turn_cancel` second, same split the plan describes) and the voice
game-detection fallback (`ServerSession.stt_request()` +
`capture.resolve_spoken_game()`, matching logic entirely local per the
plan). Fillers and every fixed spoken line (game-ask/re-ask, game
unresolved, cap reached, no-notes, "Anotado.") play from WAVs shipped in
`client/assets/` (baked once by `server/tools/bake_fixed_audio.py` - this
process has no API key to synthesize with). Deliberately NOT wired yet:
the visual overlay and tray icon (`OVERLAY`/`TRAY` default OFF here - both
packages moved unchanged and would work, just not exercised by this first
pass), the `GET /v1/games` REST call for the voice game-detect candidate list (uses
ONLY local `game_aliases.json` canonical names for now - the plan's fuller
version unions in the server's per-tester game list too). `main()` runs
`client/updater.py`'s startup update check first (a no-op outside a clone
of the public client repo).

Run with ``python -m client.main`` from the repo root, against a running
``python -m server.main serve``. Needs ``client/.env`` with ``SERVER_URL``
and ``SERVER_AUTH_TOKEN`` (see ``client/.env.example`` and
``server/tools/mint_tester.py``).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
import uuid

from client.capture import (
    GAME_CHANGED,
    HotkeyManager,
    capture_game_window_jpeg,
    detect_game,
    load_game_aliases,
    prompt_scene,
    resolve_spoken_game,
)
from client.config import load_client_settings
from client.fixed_audio import FillerPlayer, load_fillers, load_line, mark_answer_started, play_line
from client.fixed_lines import (
    CAP_REACHED_MESSAGE, CLOSING_LINE, GAME_ASK_MESSAGE, GAME_ASK_REASK_MESSAGE, GAME_UNRESOLVED_MESSAGE,
    NO_NOTES_BAKED_HOTKEY, NO_NOTES_LINE,
)
from client.gamepad import GamepadWatcher, describe_combo
from client.listen import play_confirm_tone, play_stop_tone, preload_vad_model, record_until_silence
from client.net import ServerError, ServerSession
from client.speech import AudioOut
from client.updater import check_for_update

_VERBOSE = True


def telemetry(line: str) -> None:
    if _VERBOSE:
        print(line)


def _fmt(hotkey: str) -> str:
    return hotkey.upper()


def _prompt(label: str) -> str:
    try:
        return input(label).strip()
    except EOFError:
        return ""


def _record(settings) -> bytes | None:
    return record_until_silence(
        silence_hang=settings.mic_silence_hang, max_duration=settings.mic_max_duration,
        min_duration=settings.mic_min_duration, vad_threshold=settings.mic_vad_threshold,
        speech_pad_ms=settings.mic_speech_pad_ms, no_speech_timeout=settings.mic_no_speech_timeout,
    )


def _known_game_names(settings) -> list[str]:
    """Local `game_aliases.json` canonical names only - see this module's
    docstring on why the server's `GET /v1/games` isn't unioned in yet."""
    aliases = load_game_aliases(settings.game_aliases_path)
    return sorted({a["canonical_game"] for a in aliases if a.get("canonical_game")})


def _wait_for_game_or_trigger(settings, triggers) -> tuple[str | None, object | None]:
    """Silently wait until EITHER `detect_game()` finds a known game OR the
    player fires a trigger - ported unchanged from `main._wait_for_game_or_trigger()`
    (pure local logic, no server involvement at all)."""
    for t in triggers:
        t.clear()
    while True:
        detected = detect_game(settings.game_aliases_path)
        if detected:
            return detected, None
        fired = next((t for t in triggers if t.pressed()), None)
        if fired is not None:
            return None, fired
        time.sleep(settings.game_detect_poll_seconds)


def resolve_active_game_by_voice(settings, session: ServerSession) -> tuple[str | None, bool]:
    """Network-backed port of `main.resolve_active_game_by_voice()`: STT
    happens on the server (`ServerSession.stt_request()`), but the actual
    matching against known game names is the same local, deterministic
    `capture.resolve_spoken_game()` the single-process app already uses -
    per the approved plan, that logic never moves server-side. Returns
    `(name, is_freeform)`, `(None, False)` if nothing was ever heard at all
    or the server's cap blocked the STT call."""
    known = _known_game_names(settings)
    for attempt in (1, 2):
        print(f"  {GAME_ASK_MESSAGE if attempt == 1 else GAME_ASK_REASK_MESSAGE}")
        # Spoken, not just printed: the player is looking at the game, not
        # the console. The first client cut only printed this - a real gap.
        play_line("game_ask" if attempt == 1 else "game_reask", fallback_tone=False)
        wav = _record(settings)
        if not wav:
            continue
        try:
            text, error = session.stt_request(wav)
        except ServerError as exc:
            telemetry(f"  (erro no servidor: {exc})")
            continue
        if error == "cap_reached":
            return None, False
        if not text.strip():
            continue
        matched = resolve_spoken_game(text, known, settings.game_voice_match_min_ratio)
        if matched:
            return matched, False
        if attempt == 2:
            return text.strip(), True
    return None, False


class BargeInWatcher:
    """Client-side half of barge-in (the plan's "client-local interrupt"):
    polls every trigger (F8, F6, gamepad) while armed; on a press, calls
    `AudioOut.interrupt()` directly (the real, sub-100ms stop) and sets the
    turn's `cancel` Event, which `ServerSession.ask()`'s own pump notices
    and sends `turn_cancel` for (latency-tolerant, purely a
    stop-generating-further-tokens signal - see that method's docstring).
    Same shape as the single-process app's own `BargeInWatcher`, minus the
    local-Kimi-thread specifics that no longer apply here."""

    def __init__(self, triggers: list, poll_seconds: float = 0.03) -> None:
        self.triggers = triggers
        self.poll_seconds = poll_seconds
        self._armed = threading.Event()
        self._stop = threading.Event()
        self.fired = None
        self.fired_at: float | None = None
        self._cancel: threading.Event | None = None
        self._audio_out: AudioOut | None = None
        self._thread = threading.Thread(target=self._run, name="barge-in", daemon=True)
        self._thread.start()

    def arm(self, cancel: threading.Event, audio_out: AudioOut) -> None:
        for t in self.triggers:
            t.clear()
        self.fired = None
        self.fired_at = None
        self._cancel = cancel
        self._audio_out = audio_out
        self._armed.set()

    def disarm(self) -> None:
        self._armed.clear()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._armed.is_set():
                fired = next((t for t in self.triggers if t.pressed()), None)
                if fired is not None:
                    self.fired = fired
                    self.fired_at = time.perf_counter()
                    if self._cancel is not None:
                        self._cancel.set()
                    if self._audio_out is not None:
                        self._audio_out.interrupt()
                    self._armed.clear()
            time.sleep(self.poll_seconds)


def _speak_line(name: str, barge: "BargeInWatcher | None", *, fallback_tone: bool = True):
    """Speaks a baked end-of-turn line ("Anotado.", "Beleza!", no-notes, cap)
    and lets any trigger cut it: the player's next command outranks the
    confirmation. Returns the trigger that interrupted it (handled by the
    loop as its next command, like any barge-in) or None. Without barge-in
    (BARGE_IN=false) it plays through, as before."""
    pcm = load_line(name)
    if pcm is None:
        if fallback_tone:
            play_stop_tone()
        return None
    out = AudioOut()
    cancel = threading.Event()
    try:
        out.open()
    except Exception:  # noqa: BLE001 - no output device: the text was already printed
        out.close(drain=False)
        return None
    if barge is not None:
        barge.arm(cancel, out)
    out.write(pcm, cancel)
    if cancel.is_set():
        out.interrupt()
    else:
        out.close(drain=True)
    fired = barge.fired if (barge is not None and cancel.is_set()) else None
    if barge is not None:
        barge.disarm()
    return fired


def _remember_note(session: ServerSession, settings, game: str, barge: "BargeInWatcher | None"):
    """The F6 flow - see `client.net.ServerSession.remember()`'s docstring
    for the `on_state("ouvindo")` -> record -> `turn_continue` protocol.
    Returns a trigger pressed during the closing line (the loop's next
    command), or None."""
    print(f"  Anotando uma nota pessoal para '{game}'...")
    screenshot = None
    try:
        screenshot = capture_game_window_jpeg(settings.game_aliases_path)
    except Exception as exc:  # noqa: BLE001
        telemetry(f"  (captura falhou: {exc})")

    print(f"  Fale o que quer anotar (para sozinho após {settings.mic_silence_hang:.1f}s de silêncio)...")
    wav = _record(settings)
    if not wav:
        print("  (não entendi a nota — nada foi salvo; aperte F6 pra tentar de novo)")
        play_stop_tone()
        return

    turn_id = uuid.uuid4().hex
    cancel = threading.Event()
    audio_out = AudioOut()
    try:
        audio_out.open()
    except Exception as exc:  # noqa: BLE001
        print(f"  (sem saída de áudio: {exc})")

    def record_reply() -> bytes | None:
        print("  Responda por voz: sim ou não...")
        return _record(settings)

    def on_audio(pcm: bytes, _turn_seq: int) -> None:
        audio_out.write(pcm, cancel)

    try:
        result = session.remember(
            turn_id, game, wav, screenshot_bytes=screenshot, record_reply=record_reply,
            cancel=cancel, on_audio=on_audio,
        )
    except ServerError as exc:
        print(f"  ERRO: {exc}")
        audio_out.close()
        return
    audio_out.close(drain=True)

    outcome = result.get("outcome")
    if outcome == "note_saved":
        print(f'  Anotado: "{result.get("answer_text", "")}"')
        play_confirm_tone()
        return _speak_line("note_saved", barge, fallback_tone=False)
    if outcome == "note_discarded":
        reason = result.get("stats", {}).get("reason", "")
        print(f"  Nota descartada ({reason}).")
        play_stop_tone()
    elif outcome == "note_empty":
        print("  (não entendi a nota — nada foi salvo)")
        play_stop_tone()
    elif outcome == "cap_reached":
        print(f"  {CAP_REACHED_MESSAGE} (nota não salva)")
        return _speak_line("cap_reached", barge)
    else:
        telemetry(f"  (resultado inesperado: {outcome})")
    return None


def cmd_ask(_args: argparse.Namespace) -> int:
    settings = load_client_settings(require_server=True)
    print(f"Conectando a {settings.server_url}...")
    session = ServerSession(settings.server_url, settings.server_auth_token)
    try:
        display_name = session.connect()
    except ServerError as exc:
        print(f"ERRO: {exc}")
        return 1
    print(f"Conectado como '{display_name}'.")

    preload_vad_model()
    manager = HotkeyManager(settings.capture_hotkey)
    remember_manager = HotkeyManager(settings.remember_hotkey)
    gamepad = GamepadWatcher(settings.gamepad_combo)
    all_triggers = [manager, remember_manager] + ([gamepad] if gamepad.available else [])

    print(f"Hotkey: {_fmt(manager.hotkey)}  ->  screenshot + grava a pergunta falada")
    print(f"Hotkey: {_fmt(remember_manager.hotkey)}  ->  anota uma nota pessoal (não responde)")
    if gamepad.available:
        print(f"Controle: segure {describe_combo(gamepad.combo)} — mesma ação do hotkey de pergunta")
    print(
        "  Dica: rode o jogo em 'janela sem bordas' / 'fullscreen em janela'. Em\n"
        "  fullscreen exclusivo o hotkey global pode não ser detectado."
    )

    # All three triggers, as in the original app: F6 mid-answer interrupts
    # and goes straight to taking a note (pending_fired carries which one).
    # The first client cut watched F8/gamepad only - F6 did nothing.
    barge = BargeInWatcher(all_triggers) if settings.barge_in else None

    fillers = None
    if settings.filler_enabled:
        fillers = FillerPlayer(load_fillers(), settings.filler_delay_ms / 1000.0)
        if not fillers.enabled:
            telemetry("  (sem áudio de filler em client/assets - rode "
                      "`python -m server.tools.bake_fixed_audio`)")
            fillers = None

    active_game: str | None = None
    pending_fired = None

    while True:
        detected = detect_game(settings.game_aliases_path)
        if detected:
            if detected != active_game:
                print(f"  Jogo detectado: {detected}")
            active_game = detected
            game = active_game
        elif active_game:
            game = active_game
        else:
            found, fired = _wait_for_game_or_trigger(settings, all_triggers)
            if found:
                active_game = found
                game = found
                print(f"  Jogo detectado: {found}")
            else:
                resolved, is_freeform = resolve_active_game_by_voice(settings, session)
                if resolved:
                    active_game = resolved
                    game = resolved
                    label = "sem conteúdo curado" if is_freeform else "confirmado por voz"
                    print(f"  Jogo ({label}): {resolved}")
                else:
                    print(f"  {GAME_UNRESOLVED_MESSAGE}")
                    play_line("game_unresolved")
                    print("-" * 48)
                    prompt_scene(all_triggers, "Aperte F8 ou F6 pra tentar de novo: ")
                    continue

        def _foreground_game_changed() -> bool:
            return detect_game(settings.game_aliases_path) not in (None, active_game)

        scene_prompt = (
            f"Cena — {_fmt(manager.hotkey)} pra perguntar por voz, {_fmt(remember_manager.hotkey)} "
            "pra anotar algo, digite a cena, ou digite 'jogo' pra escolher o jogo manualmente: "
        )
        if pending_fired is not None:
            scene, fired = "", pending_fired
            pending_fired = None
            print(f"  (interrompido — {'anotar' if fired is remember_manager else 'nova pergunta'})")
        else:
            scene, fired = prompt_scene(
                all_triggers, scene_prompt,
                game_changed=_foreground_game_changed, game_poll_seconds=settings.game_detect_poll_seconds,
            )

        if fired is GAME_CHANGED:
            print("-" * 48)
            continue

        if fired is None and scene.strip().lower() == "jogo":
            new_game = _prompt("Jogo: ")
            if not new_game:
                print("Até a próxima!")
                session.close()
                return 0
            active_game = new_game
            continue

        if fired is remember_manager:
            pending_fired = _remember_note(session, settings, game, barge)
            print("-" * 48)
            continue

        used_trigger = fired is not None
        question_text = None
        screenshot = None
        wav = None
        if used_trigger:
            print("  Capturando a tela...")
            try:
                screenshot = capture_game_window_jpeg(settings.game_aliases_path)
            except Exception as exc:  # noqa: BLE001
                print("  (não consegui capturar a tela — seguindo sem ela)")
                telemetry(f"  (captura falhou: {exc})")
            print(f"  Fale a sua pergunta (para sozinho após {settings.mic_silence_hang:.1f}s de silêncio)...")
            wav = _record(settings)
            if not wav:
                print("  (sem pergunta por voz — digite abaixo)")
                question_text = _prompt("Pergunta: ")
        else:
            question_text = scene

        if not wav and not question_text:
            print("  (pergunta vazia — tente de novo)\n")
            continue

        t_sent = time.perf_counter()  # recording done (silence hang included) -> request goes out
        t_first_heard: float | None = None
        turn_id = uuid.uuid4().hex
        cancel = threading.Event()
        audio_out = AudioOut()
        try:
            audio_out.open()
        except Exception as exc:  # noqa: BLE001
            print(f"  (sem saída de áudio: {exc})")
        if barge is not None:
            barge.arm(cancel, audio_out)

        filler_timer = None
        answer_audio_started = False

        def on_state(state: str) -> None:
            nonlocal filler_timer
            telemetry(f"  [{state}]")
            # "pensando" arrives after server-side STT + vision, right before
            # retrieval/Kimi - the same point the original app armed its
            # filler (the Kimi call), so FILLER_DELAY_MS means the same thing.
            if state == "pensando" and fillers is not None and filler_timer is None:
                filler_timer = fillers.arm(audio_out, cancel)

        def on_meta(question: str, scene_text: str) -> None:
            print(f'  Você perguntou: "{question}"')
            if scene_text:
                print(f"  Cena detectada: {scene_text}")

        def on_audio(pcm: bytes, _turn_seq: int) -> None:
            nonlocal answer_audio_started, t_first_heard
            if not answer_audio_started:
                answer_audio_started = True
                t_first_heard = time.perf_counter()
                if not mark_answer_started(audio_out, cancel):
                    return
            audio_out.write(pcm, cancel)

        try:
            result = session.ask(
                turn_id, game, wav_bytes=wav, question_text=question_text, screenshot_bytes=screenshot,
                cancel=cancel, on_state=on_state, on_meta=on_meta, on_audio=on_audio,
            )
        except ServerError as exc:
            print(f"  ERRO: {exc}")
            if filler_timer is not None:
                filler_timer.cancel()
            if barge is not None:
                barge.disarm()
            audio_out.close()
            print("-" * 48)
            continue
        if filler_timer is not None:
            # A turn with no answer audio (no-notes, cap) must not get a
            # filler after the fact announcing an answer that isn't coming.
            filler_timer.cancel()

        # Disarm AFTER playback finishes draining, not right when the
        # network turn ends (`session.ask()` returning just means
        # turn_result arrived - real audio can still be sitting in
        # AudioOut's local buffer for several more seconds of playback).
        # Disarming here first was a real regression from the original
        # app's ordering (main.py's stream_answer_and_speak(), which
        # disarms only after `out.close(drain=True)`) - it silently closed
        # the barge-in window for the entire local-playback tail, found on
        # a live pass where F8 had no effect while the answer was still
        # audibly speaking.
        if cancel.is_set():
            audio_out.interrupt()
            pending_fired = barge.fired if barge is not None else None
        else:
            audio_out.close(drain=True)
            if cancel.is_set():  # barge-in landed during the drain itself
                pending_fired = barge.fired if barge is not None else None
        if barge is not None:
            barge.disarm()

        print(f"  Companion: {result.get('answer_text', '')}")
        outcome = result.get("outcome")
        if pending_fired is None:
            # Server sends no audio for these - the client speaks them from
            # shipped assets, interruptible: a trigger pressed while one
            # plays cuts it and becomes the next command (pending_fired).
            if outcome == "cap_reached":
                print(f"  {CAP_REACHED_MESSAGE}")
                pending_fired = _speak_line("cap_reached", barge)
            elif outcome == "answered_no_audio":
                print("  (sem cota pra falar a resposta — ela fica no texto acima)")
                pending_fired = _speak_line("cap_reached", barge)
            elif outcome == "no_notes" and settings.remember_hotkey.lower() == NO_NOTES_BAKED_HOTKEY:
                pending_fired = _speak_line(NO_NOTES_LINE, barge)
            elif outcome == "closing":
                pending_fired = _speak_line(CLOSING_LINE, barge)
        timing = result.get("stats", {}).get("timing") or {}
        if timing or t_first_heard is not None:
            # Where a turn's wait goes. "espera de silêncio" is the mic's
            # MIC_SILENCE_HANG, paid after you stop talking and before any of
            # this starts; "até a 1ª voz" is send -> first answer audio here.
            parts = [f"espera de silêncio {settings.mic_silence_hang:.1f}s"] if wav else []
            parts += [f"{k} {v:.2f}s" for k, v in timing.items() if k != "total"]
            if t_first_heard is not None:
                parts.append(f"até a 1ª voz {t_first_heard - t_sent:.2f}s")
            telemetry("  tempo: " + " · ".join(parts))
        tts_error = result.get("stats", {}).get("tts_error")
        if tts_error:
            # A real gap this port had: the server already reports this
            # (server/session.py's _stream_kimi_and_speak()/_speak_and_report()
            # put it in result["stats"]), but nothing here ever read it, so
            # a real TTS failure looked identical to "no audio for some
            # other silent reason" - found on a live pass where the tester
            # couldn't tell why a reply had no voice.
            print(f"  (voz falhou nessa resposta: {tts_error})")
        print("-" * 48)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m client.main")
    args = ap.parse_args(argv)
    if not os.environ.get("_GC_JUST_UPDATED") and check_for_update():
        # Run the freshly pulled code as a child in this same console (a
        # plain subprocess, not os.execv - on Windows execv detaches from
        # the console). The flag stops the child from checking again.
        env = dict(os.environ, _GC_JUST_UPDATED="1")
        return subprocess.call([sys.executable, "-m", "client.main", *(argv or [])], env=env)
    try:
        return cmd_ask(args)
    except KeyboardInterrupt:
        print("\nAté a próxima!")
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
