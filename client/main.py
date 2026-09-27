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
    foreground_game_needs_admin,
    load_game_aliases,
    prompt_scene,
    resolve_spoken_game,
)
from client import texts, voices
from client.config import load_client_settings, load_saved_voice, save_token
from client.fixed_audio import (
    FillerPlayer, load_fillers, load_line, mark_answer_started, play_before_answer, play_line,
)
from client.fixed_lines import CLOSING_LINE, NO_NOTES_BAKED_HOTKEY, NO_NOTES_LINE, WEB_SEARCH_STILL_AFTER
from client.texts import t
from client.gamepad import GamepadWatcher, describe_combo
from client.listen import play_confirm_tone, play_stop_tone, preload_vad_model, record_until_silence
from client.net import AuthError, ServerError, ServerSession
from client.speech import AudioOut
from client.updater import check_for_update

_VERBOSE = True
# The picked voice (client/voices.py key): which baked lines play, and - via
# its language - the console text and what the server speaks and hears.
# Set in cmd_ask(), re-read between turns so the settings window applies live.
_voice = voices.DEFAULT_VOICE


def _set_voice(key: str) -> None:
    global _voice
    _voice = voices.get(key).key
    texts.set_language(voices.get(_voice).language)


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


def _announce_game(settings, name: str) -> None:
    """Printed on every newly detected game; plus a spoken warning when the
    game runs as administrator and this client doesn't (keys would be
    invisible to it - see capture.foreground_game_needs_admin())."""
    print(t("game_detected", name=name))
    if foreground_game_needs_admin(settings.game_aliases_path):
        print(t("attention", text=texts.spoken("game_needs_admin")))
        play_line("game_needs_admin", _voice, fallback_tone=False)


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
        line = "game_ask" if attempt == 1 else "game_reask"
        print(f"  {texts.spoken(line)}")
        # Spoken, not just printed: the player is looking at the game, not
        # the console. The first client cut only printed this - a real gap.
        play_line(line, _voice, fallback_tone=False)
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
    pcm = load_line(name, _voice)
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
    print(t("noting", game=game))
    screenshot = None
    try:
        screenshot = capture_game_window_jpeg(settings.game_aliases_path)
    except Exception as exc:  # noqa: BLE001
        telemetry(f"  (captura falhou: {exc})")

    print(t("speak_note", hang=settings.mic_silence_hang))
    wav = _record(settings)
    if not wav:
        print(t("note_not_heard_retry"))
        play_stop_tone()
        return

    turn_id = uuid.uuid4().hex
    cancel = threading.Event()
    audio_out = AudioOut()
    try:
        audio_out.open()
    except Exception as exc:  # noqa: BLE001
        print(t("no_audio_out", error=exc))

    def record_reply() -> bytes | None:
        print(t("answer_yes_no"))
        return _record(settings)

    def on_audio(pcm: bytes, _turn_seq: int) -> None:
        audio_out.write(pcm, cancel)

    try:
        result = session.remember(
            turn_id, game, wav, screenshot_bytes=screenshot, record_reply=record_reply,
            cancel=cancel, on_audio=on_audio,
        )
    except ServerError as exc:
        print(t("error", error=exc))
        audio_out.close()
        return
    audio_out.close(drain=True)

    outcome = result.get("outcome")
    if outcome == "note_saved":
        print(t("note_saved", text=result.get("answer_text", "")))
        play_confirm_tone()
        return _speak_line("note_saved", barge, fallback_tone=False)
    if outcome == "note_discarded":
        reason = result.get("stats", {}).get("reason", "")
        print(t("note_discarded", reason=reason))
        play_stop_tone()
    elif outcome == "note_empty":
        print(t("note_not_heard"))
        play_stop_tone()
    elif outcome == "cap_reached":
        print(t("note_cap", text=texts.spoken("cap_reached")))
        return _speak_line("cap_reached", barge)
    else:
        telemetry(f"  (resultado inesperado: {outcome})")
    return None


def _ask_for_token(reason: str) -> bool:
    """Opens the first-launch token window (its own process - client.main
    never imports Qt) and waits for it. True once a token the server
    accepts has been saved. Falls back to a console prompt if the window
    can't open at all (no display, Qt missing)."""
    try:
        code = subprocess.call([sys.executable, "-m", "client.gui.token_dialog", "--reason", reason])
    except OSError:
        code = 2
    if code in (0, 1):
        return code == 0
    token = _prompt(t("paste_token")).replace("SERVER_AUTH_TOKEN=", "").strip()
    if token:
        save_token(token)
    return bool(token)


def cmd_ask(_args: argparse.Namespace) -> int:
    settings = load_client_settings()
    _set_voice(settings.voice)
    if not settings.server_auth_token and not _ask_for_token("missing"):
        print(t("no_token_bye"))
        return 1
    settings = load_client_settings()  # the first-launch window also picks the voice
    _set_voice(settings.voice)
    print(t("connecting", url=settings.server_url))
    while True:
        session = ServerSession(settings.server_url, settings.server_auth_token, voice=_voice)
        try:
            display_name = session.connect()
            break
        except AuthError:
            # The saved token was revoked or mistyped: ask for a new one
            # instead of just failing. A token from client/.env (development)
            # is the developer's to fix, not a window's.
            if os.environ.get("SERVER_AUTH_TOKEN") or not _ask_for_token("rejected"):
                print(t("token_rejected"))
                return 1
            settings = load_client_settings()
        except ServerError as exc:
            print(t("error", error=exc).strip())
            print(t("server_down"))
            return 1
    print(t("connected", name=display_name))

    preload_vad_model()
    manager = HotkeyManager(settings.capture_hotkey)
    remember_manager = HotkeyManager(settings.remember_hotkey)
    gamepad = GamepadWatcher(settings.gamepad_combo)
    all_triggers = [manager, remember_manager] + ([gamepad] if gamepad.available else [])

    print(t("hotkey_ask", key=_fmt(manager.hotkey)))
    print(t("hotkey_note", key=_fmt(remember_manager.hotkey)))
    if gamepad.available:
        print(t("gamepad", combo=describe_combo(gamepad.combo)))
    print(t("tip"))

    # All three triggers, as in the original app: F6 mid-answer interrupts
    # and goes straight to taking a note (pending_fired carries which one).
    # The first client cut watched F8/gamepad only - F6 did nothing.
    barge = BargeInWatcher(all_triggers) if settings.barge_in else None

    def make_fillers():
        if not settings.filler_enabled:
            return None
        player = FillerPlayer(load_fillers(_voice), settings.filler_delay_ms / 1000.0)
        if not player.enabled:
            telemetry("  (sem áudio de filler em client/assets - rode "
                      "`python -m server.tools.bake_fixed_audio`)")
            return None
        return player

    fillers = make_fillers()

    active_game: str | None = None
    pending_fired = None

    while True:
        # A voice picked in the settings window ("Parça - Configurações")
        # while this copy runs applies from the next turn on.
        saved_voice = load_saved_voice()
        if saved_voice != _voice:
            _set_voice(saved_voice)
            session.voice = _voice
            fillers = make_fillers()
            print(t("voice_changed", name=voices.get(_voice).name))

        detected = detect_game(settings.game_aliases_path)
        if detected:
            if detected != active_game:
                _announce_game(settings, detected)
            active_game = detected
            game = active_game
        elif active_game:
            game = active_game
        else:
            found, fired = _wait_for_game_or_trigger(settings, all_triggers)
            if found:
                active_game = found
                game = found
                _announce_game(settings, found)
            else:
                resolved, is_freeform = resolve_active_game_by_voice(settings, session)
                if resolved:
                    active_game = resolved
                    game = resolved
                    print(t("game_by_voice_free" if is_freeform else "game_by_voice", name=resolved))
                else:
                    print(f"  {texts.spoken('game_unresolved')}")
                    play_line("game_unresolved", _voice)
                    print("-" * 48)
                    prompt_scene(all_triggers, t("retry_prompt"))
                    continue

        def _foreground_game_changed() -> bool:
            return detect_game(settings.game_aliases_path) not in (None, active_game)

        scene_prompt = t("scene_prompt", ask=_fmt(manager.hotkey), note=_fmt(remember_manager.hotkey))
        if pending_fired is not None:
            scene, fired = "", pending_fired
            pending_fired = None
            print(t("interrupted_note" if fired is remember_manager else "interrupted_ask"))
        else:
            scene, fired = prompt_scene(
                all_triggers, scene_prompt,
                game_changed=_foreground_game_changed, game_poll_seconds=settings.game_detect_poll_seconds,
            )

        if fired is GAME_CHANGED:
            print("-" * 48)
            continue

        if fired is None and scene.strip().lower() == "jogo":
            new_game = _prompt(t("game_prompt"))
            if not new_game:
                print(t("bye"))
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
            print(t("capturing"))
            try:
                screenshot = capture_game_window_jpeg(settings.game_aliases_path)
            except Exception as exc:  # noqa: BLE001
                print(t("capture_failed"))
                telemetry(f"  (captura falhou: {exc})")
            print(t("speak_question", hang=settings.mic_silence_hang))
            wav = _record(settings)
            if not wav:
                print(t("no_voice_type"))
                question_text = _prompt(t("question_prompt"))
        else:
            question_text = scene

        if not wav and not question_text:
            print(t("empty_question"))
            continue

        t_sent = time.perf_counter()  # recording done (silence hang included) -> request goes out
        t_first_heard: float | None = None
        turn_id = uuid.uuid4().hex
        cancel = threading.Event()
        audio_out = AudioOut()
        try:
            audio_out.open()
        except Exception as exc:  # noqa: BLE001
            print(t("no_audio_out", error=exc))
        if barge is not None:
            barge.arm(cancel, audio_out)

        filler_timer = None
        search_timers: list = []
        answer_audio_started = False

        def on_state(state: str) -> None:
            nonlocal filler_timer
            telemetry(f"  [{state}]")
            # "pensando" arrives after server-side STT + vision, right before
            # retrieval/Kimi - the same point the original app armed its
            # filler (the Kimi call), so FILLER_DELAY_MS means the same thing.
            if state == "pensando" and fillers is not None and filler_timer is None:
                filler_timer = fillers.arm(audio_out, cancel)
            # "pesquisando": no notes, the server is searching the web (3-10 s
            # of silence otherwise). Say so now - instead of a filler not yet
            # played - and once more if it drags on. Both give way to the
            # answer the moment it starts.
            elif state == "pesquisando" and not search_timers:
                if filler_timer is not None:
                    filler_timer.cancel()
                for name, delay in (("web_search", 0.0), ("web_search_still", WEB_SEARCH_STILL_AFTER)):
                    pcm = load_line(name, _voice)
                    if pcm is not None:
                        search_timers.append(play_before_answer(audio_out, cancel, pcm, delay))

        def on_meta(question: str, scene_text: str) -> None:
            print(t("you_asked", question=question))
            if scene_text:
                print(t("scene_detected", scene=scene_text))

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
            print(t("error", error=exc))
            for timer in [filler_timer, *search_timers]:
                if timer is not None:
                    timer.cancel()
            if barge is not None:
                barge.disarm()
            audio_out.close()
            print("-" * 48)
            continue
        # A turn with no answer audio (no-notes, cap) must not get a filler
        # or search line after the fact announcing an answer that isn't coming.
        for timer in [filler_timer, *search_timers]:
            if timer is not None:
                timer.cancel()

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

        print(t("answer", text=result.get("answer_text", "")))
        outcome = result.get("outcome")
        if pending_fired is None:
            # Server sends no audio for these - the client speaks them from
            # shipped assets, interruptible: a trigger pressed while one
            # plays cuts it and becomes the next command (pending_fired).
            if outcome == "cap_reached":
                print(f"  {texts.spoken('cap_reached')}")
                pending_fired = _speak_line("cap_reached", barge)
            elif outcome == "answered_no_audio":
                print(t("no_quota_voice"))
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
            print(t("voice_failed", error=tts_error))
        print("-" * 48)


_INSTANCE_MUTEX = None  # held for the whole process lifetime


def _claim_single_instance() -> bool:
    """False if another copy of the client is already running in this
    Windows session. Two copies both hook F8/F6 and answer every question
    twice, over each other, at double the cost - exactly what the first
    tester's session did (the installer's own launch plus the desktop icon).
    A named mutex; an ACCESS_DENIED answer means an elevated copy owns it.
    Anything unexpected lets the client start."""
    global _INSTANCE_MUTEX
    try:
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        handle = k32.CreateMutexW(None, False, "Local\\ParcaClient")
        err = ctypes.get_last_error()
    except Exception:  # noqa: BLE001 - not Windows, ctypes failure
        return True
    if not handle:
        return err != 5  # ERROR_ACCESS_DENIED
    if err == 183:  # ERROR_ALREADY_EXISTS
        return False
    _INSTANCE_MUTEX = handle
    return True


SETTINGS_SHORTCUT_NAME = "Parça - Configurações.lnk"


def _ensure_settings_shortcut() -> None:
    """Once per installed PC: a desktop icon that opens the settings window
    (language, voice, token) - `Parca.cmd --settings`. Installs made before
    v0.1.6 never had it; recorded in settings.json so a tester who deletes
    it doesn't get it back. Best-effort and silent."""
    from client.config import _read_user_settings, save_user_settings
    from client.updater import INSTALL_MARKER, REPO_ROOT

    if not INSTALL_MARKER.exists() or _read_user_settings().get("settings_shortcut"):
        return
    script = (
        "$d = [Environment]::GetFolderPath('Desktop'); $p = Join-Path $d $env:PARCA_LNK; "
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($p); "
        "$s.TargetPath = (Join-Path $env:PARCA_HOME 'Parca.cmd'); $s.Arguments = '--settings'; "
        "$s.WorkingDirectory = $env:PARCA_HOME; $s.Save()"
    )
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            env=dict(os.environ, PARCA_LNK=SETTINGS_SHORTCUT_NAME, PARCA_HOME=str(REPO_ROOT)),
            capture_output=True, timeout=20,
        ).returncode == 0
    except Exception:  # noqa: BLE001
        done = False
    if done:
        save_user_settings(settings_shortcut=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m client.main")
    ap.add_argument("--settings", action="store_true",
                    help="open the settings window (language, voice, token) and exit")
    args = ap.parse_args(argv)
    if args.settings:
        # Its own process, like at first launch; a running copy picks the new
        # voice up from its next turn. Always 0, so Parca.cmd doesn't pause.
        subprocess.call([sys.executable, "-m", "client.gui.token_dialog", "--reason", "settings"])
        return 0
    if not os.environ.get("_GC_JUST_UPDATED") and check_for_update():
        # Run the freshly pulled code as a child in this same console (a
        # plain subprocess, not os.execv - on Windows execv detaches from
        # the console). The flag stops the child from checking again.
        env = dict(os.environ, _GC_JUST_UPDATED="1")
        return subprocess.call([sys.executable, "-m", "client.main", *(argv or [])], env=env)
    # After the update step: a relaunching parent never gets here, so only
    # the copy that actually runs holds the lock.
    if not _claim_single_instance():
        _set_voice(load_saved_voice())
        print(texts.spoken("already_running"))
        play_line("already_running", _voice, fallback_tone=False)
        return 1
    _ensure_settings_shortcut()
    try:
        return cmd_ask(args)
    except KeyboardInterrupt:
        print("\n" + t("bye"))
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
