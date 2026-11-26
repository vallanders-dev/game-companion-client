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

**Desktop UI (stage 1, 2026-09-29):** the in-game orb, a main window, a tray
icon with Pausar/Configurações/Sair and notices now run in the UI process
(client/gui/ui_process.py, driven through `_UI`, a HudClient) and are ON by
default. An installed copy starts with no console at all (the desktop icon
runs pythonw.exe; console output goes to output/parca.log), so nothing here
may depend on typed input: `_prompt()` returns "" without a console.

Run with ``python -m client.main`` from the repo root, against a running
``python -m server.main serve``. Needs ``client/.env`` with ``SERVER_URL``
and ``SERVER_AUTH_TOKEN`` (see ``client/.env.example`` and
``server/tools/mint_tester.py``).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid

from client import capture
from client.capture import (
    GAME_CHANGED,
    HotkeyManager,
    NoForegroundGame,
    ScreenBlockedGame,
    capture_game_window_jpeg,
    detect_game,
    foreground_game_needs_admin,
    is_no_game_answer,
    load_game_aliases,
    log_capture_failure,
    prompt_scene,
    remember_game_for_foreground,
    resolve_spoken_game,
)
from client import brewing_icons, build_assets, game_context, play_history, texts, voices
from client.config import (DEFAULT_SERVER_URL, FALLBACK_SERVER_URLS, load_client_settings, load_follow_up_seconds,
                           load_saved_voice, load_voice_commands, save_token)
from client.fixed_audio import (
    FillerPlayer, load_fillers, load_line, mark_answer_started, play_before_answer, play_line, wait_line_allowed,
)
from client.fixed_lines import (CLOSING_LINE, NO_NOTES_BAKED_HOTKEY, NO_NOTES_LINE, WAIT_LINE_COOLDOWN,
                                 WEB_SEARCH_STILL_AFTER)
from client.texts import t
from client.gamepad import GamepadWatcher, describe_combo
from client.gui.client import ROOT, HudClient
from client.listen import play_confirm_tone, play_stop_tone, preload_vad_model, record_until_silence
from client.net import AuthError, LiveTranscript, ServerError, ServerSession
from client.recap_trigger import RecapTrigger
from client.chat_inbox import ChatInbox
from client import fixed_audio, own_voice
from client.speech import AudioOut
from client.launcher import install_launcher, relaunch_as_ombri, windowed_python
from client.updater import check_for_update
from client.voice_commands import VoiceListener, guide_word, listener_language

_VERBOSE = True
# The picked voice (client/voices.py key): which baked lines play, and - via
# its language - the console text and what the server speaks and hears.
# Set in cmd_ask(), re-read between turns so the settings window applies live.
_voice = voices.DEFAULT_VOICE
# The desktop UI (orb, main window, tray). A disabled HudClient until
# cmd_ask() starts the real one - every method is a safe no-op either way.
_UI = HudClient(False)
# The player's own key failed: a notice with "Abrir configurações", and its card says why.
own_voice.on_failed = lambda category, code, detail: _UI.notice("own_key", category=category, code=code,
                                                                 detail=detail)
fixed_audio.LINE_OVERRIDE = own_voice.cached_line  # the fixed lines in the player's own voice, once made
# Set from the tray's "Pausar o Ombri": triggers are ignored until resumed.
_PAUSED = threading.Event()
# What the PC chat sends (typed questions, typed notes, the mic button, the
# note's yes/no buttons): docs/chat_redesign.md, 2026-11-23.
_CHAT = ChatInbox()
_CHAT.on_dropped = lambda: _UI.chat_done("failed")
# A --relaunched copy (admin relaunch, update restart) starts in the tray: the
# game is usually in front, and opening the main window took its focus.
_RELAUNCHED = False
_SESSION: "ServerSession | None" = None
# A game picked from the list or typed in the window (owner, 2026-11-12): set by
# the UI's "game_set:<name>" command, taken by the main loop at its next poll.
_PICKED_GAME: dict = {"name": None}
_GAME_PICKED = threading.Event()
GAME_NAME_MAX = 80
# "Ei, Ombri" / "chega" (client/voice_commands.py): None unless the setting is on.
_VOICE: "VoiceListener | None" = None
# The Build pane's state (client/gui/build_pane.py; server/build_guide.py,
# 2026-11-22), mirrored from the UI's "guide_state:<id>:<layer>[:list]"
# commands and from every `guide` message: what the next turn_start.guide says.
# The pane is the keeper; this is a copy, nothing is written anywhere.
_GUIDE: dict = {"id": None, "layer": 0, "list": False}
# The in-game pane's state (client/gui/game_pane.py; the owner's visual-pane
# brief, 2026-11-24), mirrored from the UI's "pane_state:<json>" commands:
# what the next turn_start.visual says, so the server's matcher (PR 3) can
# read "outra", "essa", "fecha" with the pane in mind. A copy of what the
# pane reports - never the pictures' bytes, never a question.
_PANE: dict = {}
PANE_STATE_KEYS = ("open", "kind", "folded", "index", "count", "pinned", "title", "query", "style", "guide")


def _set_voice(key: str) -> None:
    global _voice
    _voice = voices.get(key).key
    texts.set_language(voices.get(_voice).language)
    # The mic's start cue is this voice saying "Yes?" (the beep if not baked).
    from client.listen import set_start_cue
    from client.speech import TTS_PCM_RATE

    set_start_cue(load_line("ack", _voice), TTS_PCM_RATE)


def telemetry(line: str) -> None:
    if _VERBOSE:
        print(line)


def _fmt(hotkey: str) -> str:
    return hotkey.upper()


def _has_console() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _prompt(label: str) -> str:
    if not _has_console():  # pythonw launch: nobody can type an answer
        return ""
    try:
        return input(label).strip()
    except (EOFError, RuntimeError):
        return ""


@contextlib.contextmanager
def _mic_busy():
    """The voice listener ignores the mic while the client records."""
    voice = _VOICE
    previous = voice.mode if voice is not None else None
    if voice is not None:
        voice.set_mode(None)
    try:
        yield
    finally:
        if voice is not None:
            voice.set_mode(previous)


def _record(settings, live=None, on_speech=None) -> bytes | None:
    with _mic_busy():
        return _record_now(settings, live, on_speech)


def _record_now(settings, live=None, on_speech=None) -> bytes | None:
    return record_until_silence(
        silence_hang=settings.mic_silence_hang, max_duration=settings.mic_max_duration,
        min_duration=settings.mic_min_duration, vad_threshold=settings.mic_vad_threshold,
        speech_pad_ms=settings.mic_speech_pad_ms, no_speech_timeout=settings.mic_no_speech_timeout,
        smart_turn=getattr(settings, "smart_turn", True), live=live, on_speech=on_speech,
    )


def _live(session, settings) -> LiveTranscript | None:
    """Live words for the next recording (0.1.19, client/net.py LiveTranscript):
    shorter waits, no cut on "uhm", the text ready as soon as you stop.
    LIVE_WORDS=false turns it off (the recording works exactly as in 0.1.18)."""
    if not getattr(settings, "live_words", True) or session is None:
        return None
    return LiveTranscript(session, voices.get(_voice).language)


def _live_result(live, wav) -> str | None:
    """Ends the live take; the id to send instead of the WAV, or None."""
    if live is None:
        return None
    text = live.finish()
    if text and wav:
        telemetry(f"  (ouvido ao vivo: {text!r})")
        return live.id
    return None


# The follow-up window (2026-10-14, owner-approved): after a real answer the
# mic stays open for load_follow_up_seconds() with no beep, the orb showing
# "ouvindo", so a follow-up ("e o outro?") needs no F8 - the server keeps the
# conversation (server/conversation.py). Only after outcomes that answered
# something; never after "valeu" (closing), a cap line, an interrupted answer
# or while paused, so it can never chain on its own. A cough or a game sound
# must pass FOLLOW_UP_MIN_SPEECH of speech and a stricter VAD threshold
# before a paid turn starts; an F8/F6/gamepad press ends the window at once
# and becomes the next command.
FOLLOW_UP_OUTCOMES = ("answered", "no_notes")
FOLLOW_UP_MIN_SPEECH = 0.35
FOLLOW_UP_VAD_THRESHOLD = 0.6


def _listen_for_follow_up(settings, triggers: list, seconds: float,
                          session=None) -> tuple[bytes | None, object | None, str | None]:
    """(WAV of a follow-up or None, a trigger pressed during the window or None,
    the live take's id when its words were heard live)."""
    for trigger in triggers:
        trigger.clear()
    fired: list = []

    def key_pressed() -> bool:
        hit = next((trigger for trigger in triggers if trigger.pressed()), None)
        if hit is not None:
            fired.append(hit)
        return hit is not None

    _UI.state("ouvindo")
    live = _live(session, settings)
    with _mic_busy():
        wav = record_until_silence(
            silence_hang=settings.mic_silence_hang, max_duration=settings.mic_max_duration,
            min_duration=settings.mic_min_duration,
            vad_threshold=max(settings.mic_vad_threshold, FOLLOW_UP_VAD_THRESHOLD),
            speech_pad_ms=settings.mic_speech_pad_ms, no_speech_timeout=seconds,
            start_tone=False, abort=key_pressed, min_speech_onset=FOLLOW_UP_MIN_SPEECH,
            smart_turn=getattr(settings, "smart_turn", True), live=live,
        )
    live_id = _live_result(live, wav)
    _UI.state("pensando" if wav else "idle")
    return wav, (fired[0] if fired else None), live_id


def _sync_voice_listener(triggers: list) -> None:
    """Starts, stops or restarts the voice listener to match the settings
    (checked between turns, like the voice). Its two triggers live in the
    shared trigger list, so every wait loop and the barge-in watcher see them."""
    global _VOICE
    wanted = load_voice_commands()
    language = voices.get(_voice).language
    if _VOICE is not None and (not wanted or _VOICE.language != listener_language(language)):
        _VOICE.stop()
        for trigger in (_VOICE.wake, _VOICE.shush, _VOICE.guide):
            if trigger in triggers:
                triggers.remove(trigger)
        _VOICE = None
        telemetry("  (comandos de voz desligados)")
    if wanted and _VOICE is None:
        _VOICE = VoiceListener(language)
        _VOICE.set_guide(bool(_GUIDE["id"]))  # a build already open: its words from the start
        _VOICE.start()
        triggers.extend([_VOICE.wake, _VOICE.shush, _VOICE.guide])
        telemetry("  (comandos de voz ligados - modelo carregando)")


def _sync_guide_words() -> None:
    """The listener hears the pane's words ("próxima" / "volta" / "repete",
    client/voice_commands.py GUIDE_PHRASES) only while a build is open: told
    on every change of the `_GUIDE` mirror. Nothing when the setting is off."""
    voice = _VOICE
    if voice is not None:
        voice.set_guide(bool(_GUIDE["id"]))


def _known_game_names(settings) -> list[str]:
    """Local `game_aliases.json` canonical names only - see this module's
    docstring on why the server's `GET /v1/games` isn't unioned in yet."""
    aliases = load_game_aliases(settings.game_aliases_path)
    return sorted({a["canonical_game"] for a in aliases if a.get("canonical_game")})


def _voice_setting_changed() -> bool:
    """True when the settings window saved a different voice (and with it,
    maybe a different language) than the one this copy is using."""
    try:
        return load_saved_voice() != _voice
    except Exception:  # noqa: BLE001 - an unreadable settings file changes nothing
        return False


def _wait_for_game_or_trigger(settings, triggers, stop=None) -> tuple[str | None, object | None]:
    """Silently wait until EITHER `detect_game()` finds a known game OR the
    player fires a trigger - ported from `main._wait_for_game_or_trigger()`
    (pure local logic, no server involvement at all). `stop()`, polled at the
    same pace, ends the wait with (None, None) - a voice/language changed in
    the settings window, applied by the caller right away."""
    for t in triggers:
        t.clear()
    while True:
        detected = detect_game(settings.game_aliases_path)
        if detected:
            return detected, None
        if stop is not None and stop():
            return None, None
        fired = next((t for t in triggers if t.pressed()), None)
        if fired is not None and _PAUSED.is_set():
            fired.clear()
            fired = None
        if fired is not None:
            return None, fired
        time.sleep(settings.game_detect_poll_seconds)


def _announce_game(settings, name: str) -> None:
    """Printed on every newly detected game; plus a spoken warning when the
    game runs as administrator and this client doesn't (keys would be
    invisible to it - see capture.foreground_game_needs_admin())."""
    print(t("game_detected", name=name))
    _UI.game(name)
    if foreground_game_needs_admin(settings.game_aliases_path):
        print(t("attention", text=texts.spoken("game_needs_admin")))
        _UI.notice("admin")  # with a one-click "Reabrir como admin"
        play_line("game_needs_admin", _voice, fallback_tone=False)


# What `resolve_active_game_by_voice()` returns for "I'm not playing anything".
NO_GAME = "\0no-game"


def _adopt_spoken_game(resolved: str, is_freeform: bool, recap: RecapTrigger) -> None:
    """A game named by voice becomes the active one (shown, printed, and
    remembered for this PC's foreground window)."""
    _UI.game(resolved)
    if resolved != recap.game:
        recap.forget()  # named by voice: no matched window to watch yet
    print(t("game_by_voice_free" if is_freeform else "game_by_voice", name=resolved))
    # Next time this game is detected without asking (this PC
    # only; capture.py pass 4).
    remembered = remember_game_for_foreground(resolved)
    if remembered:
        telemetry(f"  (lembrado neste PC: {remembered.name} = {resolved})")


def _say_no_game() -> None:
    _UI.game(None)
    print(f"  {texts.spoken('game_none')}")
    play_line("game_none", _voice, fallback_tone=False)


def resolve_active_game_by_voice(settings, session: ServerSession) -> tuple[str | None, bool]:
    """Network-backed port of `main.resolve_active_game_by_voice()`: STT
    happens on the server (`ServerSession.stt_request()`), but the actual
    matching against known game names is the same local, deterministic
    `capture.resolve_spoken_game()` the single-process app already uses -
    per the approved plan, that logic never moves server-side. Returns
    `(name, is_freeform)`, `(None, False)` if nothing was ever heard at all
    or the server's cap blocked the STT call, `(NO_GAME, False)` when the
    player says they aren't playing anything (`capture.is_no_game_answer()`,
    checked first: on the second try "nenhum" would otherwise become a
    free-form game called "nenhum")."""
    known = _known_game_names(settings)
    # The same question as a card (owner, 2026-11-12): answer out loud, or open
    # the list and pick or type it. A pick made meanwhile wins (the caller checks).
    _UI.notice("game_ask")
    for attempt in (1, 2):
        if _GAME_PICKED.is_set():
            return None, False
        line = "game_ask" if attempt == 1 else "game_reask"
        print(f"  {texts.spoken(line)}")
        # Spoken, not just printed: the player is looking at the game, not
        # the console. The first client cut only printed this - a real gap.
        play_line(line, _voice, fallback_tone=False)
        _UI.state("ouvindo")
        wav = _record(settings)
        _UI.state("idle")
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
        if is_no_game_answer(text):
            return NO_GAME, False
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
        if _VOICE is not None:
            _VOICE.set_mode("shush")
        self._armed.set()

    def disarm(self) -> None:
        self._armed.clear()
        if _VOICE is not None:
            _VOICE.set_mode("wake")

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


def _capture_for_turn(settings) -> tuple[bytes | None, str]:
    """This turn's screenshot, plus WHY there isn't one ("" when there is).

    The reason travels with the question (client/net.py `ask(no_screen_reason=)`)
    so the ANSWER can say the true thing - "o jogo nao estava na frente" -
    instead of the old generic line, which claimed the image had not arrived
    in time even when none was ever taken (owner, 2026-11-17: a screen
    question came back as "I can't read images", which was never true).
    Every failure also goes to capture.log, because a console line in a
    window the player never sees is not evidence.

    Still no whole-monitor fallback: no recognized game in front = no
    picture, said out loud. Honest degradation, CLAUDE.md Principle 2."""
    try:
        return capture_game_window_jpeg(settings.game_aliases_path), ""
    except ScreenBlockedGame as exc:
        telemetry(f"  (sem captura: {exc})")  # by rule, not a failure: not logged as one
        return None, "jogo_competitivo"
    except NoForegroundGame as exc:
        print(t("capture_failed"))
        telemetry(f"  (captura falhou: {exc})")
        log_capture_failure(exc)
        return None, "fora_do_jogo"
    except Exception as exc:  # noqa: BLE001
        print(t("capture_failed"))
        telemetry(f"  (captura falhou: {exc})")
        log_capture_failure(exc)
        return None, "falha_na_captura"


def _typed_note(session: ServerSession, game: str, text: str) -> None:
    """A note typed in the chat (the "Lembrar" chip): the same remember turn as
    F6 with the text instead of a recording, no screenshot, nothing spoken; the
    server's "save it?" shows as buttons under the note and only a clear "Sim"
    saves (Principle 3). Needs "text_only" on the server; an older one gets
    nothing sent (the chat says it failed)."""
    if not session.supports("text_only"):
        _UI.chat_done("failed")
        return
    turn_id = uuid.uuid4().hex
    cancel = threading.Event()
    language = voices.get(_voice).language

    def record_reply() -> str | None:
        _UI.chat_confirm(text)
        return _CHAT.wait_confirm(language, cancel)

    try:
        result = session.remember(turn_id, game, None, record_reply=record_reply, cancel=cancel,
                                  on_usage=_UI.usage, note_text=text)
    except ServerError as exc:
        print(t("error", error=exc))
        _UI.connection("offline")
        _UI.chat_done("failed")
        return
    _UI.connection("ok")
    outcome = str(result.get("outcome") or "failed")
    _UI.chat_done(outcome)
    if outcome == "note_saved":
        print(t("note_saved", text=result.get("answer_text", "")))
    elif outcome == "note_discarded":
        print(t("note_discarded", reason=result.get("stats", {}).get("reason", "")))


def _remember_note(session: ServerSession, settings, game: str, barge: "BargeInWatcher | None"):
    """The F6 flow - see `client.net.ServerSession.remember()`'s docstring
    for the `on_state("ouvindo")` -> record -> `turn_continue` protocol.
    Returns a trigger pressed during the closing line (the loop's next
    command), or None."""
    print(t("noting", game=game))
    screenshot, _ = _capture_for_turn(settings)

    print(t("speak_note", hang=settings.mic_silence_hang))
    _UI.state("ouvindo")
    # the player's own key: the note never reaches our STT (a server that takes the note as words)
    own_ears = own_voice.settings_for("ears") if session.supports("text_only") else None
    live = None if own_ears else _live(session, settings)
    wav = _record(settings, live)
    live_id = _live_result(live, wav)
    _UI.state("pensando")
    note_text = None
    if wav and own_ears:
        note_text = own_voice.own_transcribe(own_ears, wav, voices.get(_voice).language)
        if note_text == "":
            wav = None  # nothing said
    if not wav:
        _UI.state("idle")
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

    # The player's own voice reads the "save it?" (the server sends it as text).
    own = own_voice.settings_for("voice") if session.supports("text_only") else None
    speaker = own_voice.OwnSpeaker(own, audio_out, cancel) if own else None

    def record_reply() -> bytes | None:
        if speaker is not None:
            speaker.wait_spoken()  # the mic opens after the question, not over it
        print(t("answer_yes_no"))
        _UI.state("ouvindo")
        reply = _record(settings)
        _UI.state("pensando")
        return reply

    def on_audio(pcm: bytes, _turn_seq: int) -> None:
        _UI.state("falando")
        audio_out.write(pcm, cancel)

    def on_text(text: str) -> None:
        if speaker is not None:
            _UI.state("falando")
            speaker.say(text)

    try:
        result = session.remember(
            turn_id, game, None if live_id or note_text else wav, screenshot_bytes=screenshot,
            record_reply=record_reply, live_id=live_id,
            cancel=cancel, on_audio=on_audio, on_usage=_UI.usage,
            note_text=note_text or None, text_only=speaker is not None, on_text=on_text,
        )
    except ServerError as exc:
        print(t("error", error=exc))
        _UI.state("idle")
        _UI.connection("offline")
        if speaker is not None:
            cancel.set()
            speaker.finish(timeout=2)
        audio_out.close()
        return
    if speaker is not None:
        speaker.finish()
    audio_out.close(drain=True)
    _UI.state("idle")
    _UI.connection("ok")

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
    elif result.get("answer_text"):
        # F6 pressed for a question: the server answered it like F8 (spoken above).
        print(t("answer", text=result["answer_text"]))
    else:
        telemetry(f"  (resultado inesperado: {outcome})")
    return None


def _offer_recap(session: ServerSession, settings, game: str, barge: "BargeInWatcher | None"):
    """The end-of-session recap (docs/player_notes.md, piece C): the game's
    process is gone, so ask the server whether there is anything from this
    session worth keeping. The server speaks the offer and runs the F6
    confirm round (`ServerSession.recap()`); `recap_nothing` is silence.
    Returns a trigger pressed during the closing line, like `_remember_note`.

    Since 2026-11-12 (owner: the spoken offer after every session got
    annoying), a server with "recap_card" sends the recap as text and it is
    shown as a card above the tray: nothing spoken, nothing heard, and only
    the card's "Sim, salva" button saves (`_save_recap_card`)."""
    print(t("recap_offer", game=game))
    turn_id = uuid.uuid4().hex
    if session.supports("recap_card"):
        try:
            result = session.recap(turn_id, game, record_reply=lambda: None, card=True)
        except ServerError as exc:
            print(t("error", error=exc))
            _UI.connection("offline")
            return None
        if result.get("outcome") == "recap_card" and result.get("answer_text"):
            _UI.notice("recap", game=game, text=result["answer_text"],
                       offer_id=str((result.get("stats") or {}).get("offer_id", "")))
        else:
            telemetry(t("recap_nothing"))
        return None
    cancel = threading.Event()
    audio_out = AudioOut()
    try:
        audio_out.open()
    except Exception as exc:  # noqa: BLE001
        print(t("no_audio_out", error=exc))

    def record_reply() -> bytes | None:
        print(t("answer_yes_no"))
        _UI.state("ouvindo")
        reply = _record(settings)
        _UI.state("pensando")
        return reply

    def on_audio(pcm: bytes, _turn_seq: int) -> None:
        _UI.state("falando")
        audio_out.write(pcm, cancel)

    try:
        result = session.recap(turn_id, game, record_reply=record_reply, cancel=cancel,
                               on_audio=on_audio, on_usage=_UI.usage)
    except ServerError as exc:
        print(t("error", error=exc))
        _UI.state("idle")
        _UI.connection("offline")
        audio_out.close()
        return None
    audio_out.close(drain=True)
    _UI.state("idle")
    _UI.connection("ok")

    outcome = result.get("outcome")
    if outcome == "note_saved":
        print(t("note_saved", text=result.get("answer_text", "")))
        play_confirm_tone()
        return _speak_line("note_saved", barge, fallback_tone=False)
    if outcome == "note_discarded":
        print(t("note_discarded", reason=result.get("stats", {}).get("reason", "")))
        play_stop_tone()
    elif outcome == "recap_nothing":
        telemetry(t("recap_nothing"))
    elif outcome == "cap_reached":
        print(t("note_cap", text=texts.spoken("cap_reached")))
        return _speak_line("cap_reached", barge)
    elif outcome == "note_save_failed":
        print(t("error", error=result.get("answer_text") or outcome))
    else:
        telemetry(f"  (resultado inesperado: {outcome})")
    return None


def _game_context_field(game: str) -> dict | None:
    """`turn_start.game_context` for session.ask(): the edition, version and
    own language of the game behind the last matched window
    (client/game_context.py), None when the question's game is not that
    window's (picked by hand, named by voice) or has no reader. Never raises:
    a slip here must not cost the question."""
    try:
        proc = capture.last_game_process()
        if not proc or proc[0] != game:
            return None
        return game_context.context_for(game, proc[1], proc[2], capture.last_game_title())
    except Exception:  # noqa: BLE001
        return None


def _guide_field() -> dict:
    """`turn_start.guide` for session.ask(): {} while the pane is closed (so
    "quero construir uma casa" can start it), {"list": True} while it lists
    the builds, {id, layer} with a build open. net.py sends it only when the
    server advertises "build_guide"."""
    if _GUIDE["id"]:
        return {"id": _GUIDE["id"], "layer": _GUIDE["layer"]}
    if _GUIDE["list"]:
        return {"list": True}
    return {}


def _visual_field() -> dict | None:
    """`turn_start.visual` for session.ask(): the in-game pane's state as the
    UI last reported it (open, kind, index/count, pinned, title, query, style,
    the guide), None before the UI ever said. net.py sends it only when the
    server advertises "visual"."""
    if not _PANE:
        return None
    return {k: _PANE[k] for k in PANE_STATE_KEYS if k in _PANE}


def _apply_visual_message(body: dict) -> None:
    """A `visual` message from the server (inside an ask turn): pictures for
    the in-game pane, or a command on it. The pane keeps the state and
    answers with "pane_state:"; nothing to mirror here beyond the telemetry
    line (the items' bytes never go anywhere but the UI)."""
    action = str(body.get("action") or ("open" if body.get("items") else "")) or "-"
    items = body.get("items") if isinstance(body.get("items"), list) else []
    telemetry(f"  [painel] {action} · {len(items)} imagem(ns) · índice {body.get('index', '-')}")
    _UI.visual(body)


def _pane_command(cmd: str) -> None:
    """The in-game pane's own commands. "pane_state:<json>" is what it shows
    now (a click, a message, a fold). "pane_hidden:fullscreen": it opened
    while a game owns the display in exclusive fullscreen, where no window
    of ours can show - say so once, honestly, instead of failing silently."""
    if cmd.startswith("pane_state:"):
        try:
            state = json.loads(cmd.removeprefix("pane_state:"))
        except ValueError:
            return
        if isinstance(state, dict):
            _PANE.clear()
            _PANE.update({k: v for k, v in state.items() if k in PANE_STATE_KEYS})
        return
    if cmd == "pane_hidden:fullscreen":
        print(t("attention", text=texts.spoken("pane_fullscreen")))
        threading.Thread(target=play_line, args=("pane_fullscreen", _voice), kwargs={"fallback_tone": False},
                         name="pane-fullscreen-line", daemon=True).start()


def _apply_guide_message(body: dict) -> None:
    """A `guide` message - the server's (inside an ask turn) or one built here
    from a REST click-open: the pane gets it, and the mirror follows at once,
    so a second command before the pane answers back still carries the right
    layer. The step's real screenshot (`shot`, server/build_shots.py) is
    remembered here too, and an `open` starts the fetch of the build's other
    pictures (the icons of its blocks, the shots of its other steps) so the
    pane's own clicks have them without a turn."""
    action = body.get("action")
    if action in ("open", "layer"):
        try:
            layer = int(body.get("layer") or 1)
        except (TypeError, ValueError):
            layer = 1
        _GUIDE.update(id=str(body.get("id") or "") or None, layer=layer, list=False)
        build_assets.remember_message_shot(body)
        if action == "open":
            _fetch_guide_assets(body)
    elif action == "close":
        _GUIDE.update(id=None, layer=0, list=False)
    elif action == "list":
        _GUIDE.update(id=None, layer=0, list=True)
    if action in ("open", "layer", "close", "list"):
        telemetry(f"  [guia] {action} · {body.get('id') or '-'} · camada {body.get('layer') or '-'}")
        _sync_guide_words()
    elif action == "step":
        # A word heard on the PC: the pane moves itself (as its keys do) and
        # answers with "guide_state:", which brings the mirror up to date.
        telemetry(f"  [guia] step {int(body.get('delta') or 0):+d} · {_GUIDE['id'] or '-'} · camada {_GUIDE['layer'] or '-'}")
    _UI.guide(body)


def _fetch_guide_assets(body: dict) -> None:
    """After a guide `open`: the chips' icons (this PC's cache first, then the
    server) and the real screenshots of the steps the server says it has
    (`shots`: {layers, angles}), each sent to the UI as it lands
    (`guide_assets` messages). On its own thread; stops when the guide is no
    longer the open one; a slow or old server (no "build_shots") just means
    the grid and colour squares, as before."""
    session = _SESSION
    guide_id = str(body.get("id") or "")
    build = body.get("build") if isinstance(body.get("build"), dict) else {}
    ids = build_assets.icon_ids(build)
    have, missing = build_assets.icons_split(ids)
    if have:
        _UI.guide_assets(guide_id, icons=have)
    names = build_assets.shot_names(body.get("shots"), int(body.get("layers") or 0))
    cached = build_assets.shots_have(guide_id, names)
    if cached:
        _UI.guide_assets(guide_id, shots=cached)
    if session is None or not session.supports("build_shots") or not (missing or names):
        return

    def work() -> None:
        if missing:
            got = build_assets.icons_fetch(session, missing)
            if got:
                _UI.guide_assets(guide_id, icons=got)
        build_assets.shots_fetch(session, guide_id, names, on_each=lambda name, b64: _UI.guide_assets(guide_id, shots={name: b64}),
                                 still_wanted=lambda: _GUIDE["id"] == guide_id)

    threading.Thread(target=work, name="guide-assets", daemon=True).start()


def _guide_by_voice(command: str | None) -> str | None:
    """A guide word heard on this PC (client/voice_commands.py `guide`:
    listening mode on, a build open, the word said alone). "next" / "back"
    move the pane here and now - no recording, no turn, no sound: the pane
    steps as its own keys do (next on the last layer finishes the build) and
    ripples as for a spoken command. "repeat" is the one word whose whole
    point is hearing the line again, so it comes back as the typed question
    the main loop sends: the server's matcher answers with this layer's
    fixed line (no STT, no Kimi, not counted as a use). None = handled, or
    nothing to do (the pane closed meanwhile)."""
    if command not in ("next", "back", "repeat") or not _GUIDE["id"]:
        return None
    word = guide_word(voices.get(_voice).language, command)
    print(t("guide_word_heard", word=word))
    if command == "repeat":
        return word
    _apply_guide_message({"action": "step", "delta": 1 if command == "next" else -1, "command": command})
    return None


def _guide_command(cmd: str) -> None:
    """The Build pane's own commands. "guide_state:<id>:<layer>[:list]" is
    where the pane is now (a click or a key moved it: no turn, nothing to
    fetch). "guide_list:<game>" (the "Construir" button) and
    "guide_open:<id>" (a build clicked in the list) read the server's REST
    (`GET /v1/build_guides[/{id}]`) on their own thread and come back as a
    `guide` message of the same shape the server sends by voice."""
    if cmd.startswith("guide_state:"):
        parts = cmd.split(":")
        try:
            layer = int(parts[2]) if len(parts) > 2 and parts[2] else 0
        except ValueError:
            layer = 0
        gid = parts[1][:64] if len(parts) > 1 else ""
        _GUIDE.update(id=gid or None, layer=layer, list=len(parts) > 3 and parts[3] == "list")
        _sync_guide_words()
        return
    session = _SESSION
    language = voices.get(_voice).language

    def work() -> None:
        try:
            if cmd.startswith("guide_list:"):
                game = " ".join(cmd.removeprefix("guide_list:").split())[:GAME_NAME_MAX]
                builds = session.list_build_guides(game, language) if session is not None else []
                _apply_guide_message({"action": "list", "command": "click", "builds": builds, "game": game})
            elif cmd.startswith("guide_open:"):
                if session is None:
                    raise ServerError("offline")
                data = session.get_build_guide(cmd.removeprefix("guide_open:")[:64], language)
                body = {k: data[k] for k in ("id", "name", "layers", "materials", "build", "materials_by_layer", "hints",
                                             "shot", "shots")
                        if k in data}
                _apply_guide_message({"action": "open", "command": "click", "layer": 1, **body})
        except ServerError as exc:
            telemetry(f"  (guia indisponível: {exc})")
            _UI.guide({"action": "error", "error": str(exc)})

    threading.Thread(target=work, name="guide-rest", daemon=True).start()


def _show_recipe(card: dict) -> None:
    """The brewing strip's card: to the window now, with the icons this PC
    already has (client/brewing_icons.py); the missing ones are fetched on
    their own thread and sent after, so a slow server never holds the strip."""
    have, missing = brewing_icons.split(card)
    _UI.recipe(card, have)
    if not missing:
        return
    session = _SESSION

    def work() -> None:
        got = brewing_icons.fetch(session, missing)
        if got:
            _UI.recipe_icons(got)
        else:
            telemetry(f"  (ícones da poção indisponíveis: {', '.join(missing)})")

    threading.Thread(target=work, name="brewing-icons", daemon=True).start()


def _notes_command(cmd: str) -> None:
    """"Minhas notas" in the window: list, or delete one then list again.
    REST next to the WebSocket (`ServerSession.list_notes/delete_note`), on
    its own thread so the UI reader thread never waits on the network."""
    session = _SESSION
    if session is None:
        _UI.notes([], error="offline")
        return

    def work() -> None:
        try:
            if cmd.startswith("notes_delete:"):
                session.delete_note(cmd.split(":", 1)[1])
            _UI.notes(session.list_notes())
        except ServerError as exc:
            _UI.notes([], error=str(exc))

    threading.Thread(target=work, name="notes-rest", daemon=True).start()


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
    _start_ui(settings)
    play_history.start()
    print(t("connecting", url=settings.server_url))
    offline_told = False
    while True:
        fallbacks = FALLBACK_SERVER_URLS if settings.server_url == DEFAULT_SERVER_URL else ()
        session = ServerSession(settings.server_url, settings.server_auth_token, voice=_voice,
                                fallback_urls=fallbacks)
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
            # No console to read an error from any more: the window shows
            # "Sem conexão, tentando de novo" and this keeps trying.
            print(t("error", error=exc).strip())
            print(t("server_down"))
            _UI.connection("offline")
            if not offline_told:
                offline_told = True
                _UI.notice("offline")
            time.sleep(RECONNECT_SECONDS)
    global _SESSION
    _SESSION = session
    print(t("connected", name=display_name))
    _UI.connection("ok")
    # Reconnects in the background whenever the connection drops, so a
    # question never waits for it (and the window's status stays true).
    def on_keepalive(status: str) -> None:
        _UI.connection("ok" if status == "ok" else "offline")
        if status == "ok":
            _UI.features(session.features)  # a reconnect may have landed on an updated server
    session.start_keepalive(on_status=on_keepalive)
    if session.usage:
        _UI.usage(*session.usage)
    _UI.features(session.features)  # "notes" shows the "Minhas notas" page

    preload_vad_model()
    if getattr(settings, "smart_turn", True):
        # first run downloads the 8 MB end-of-turn model in the background; until
        # then (or without network) the fixed silence wait is used
        from client import turn_detect
        turn_detect.start_loading()
    manager = HotkeyManager(settings.capture_hotkey)
    remember_manager = HotkeyManager(settings.remember_hotkey)
    gamepad = GamepadWatcher(settings.gamepad_combo)
    all_triggers = [manager, remember_manager, _CHAT, _CHAT.mic] + ([gamepad] if gamepad.available else [])

    print(t("hotkey_ask", key=_fmt(manager.hotkey)))
    print(t("hotkey_note", key=_fmt(remember_manager.hotkey)))
    if gamepad.available:
        print(t("gamepad", combo=describe_combo(gamepad.combo)))
    print(t("tip"))
    ui_keys = {"ask": _fmt(manager.hotkey), "note": _fmt(remember_manager.hotkey),
               "pad": _short_combo(describe_combo(gamepad.combo)) if gamepad.available else None}
    _send_ui_info(ui_keys)
    _show_announcement(session.announce)
    _show_survey(session)
    threading.Thread(target=_watch_for_updates, name="update-watch", daemon=True).start()

    # All three triggers, as in the original app: F6 mid-answer interrupts
    # and goes straight to taking a note (pending_fired carries which one).
    # The first client cut watched F8/gamepad only - F6 did nothing.
    barge = BargeInWatcher(all_triggers) if settings.barge_in else None
    _sync_voice_listener(all_triggers)

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
    own_lines_sig = None  # the player's own voice lines the fillers were loaded with

    active_game: str | None = None
    # The player answered the game question with "none" (owner, 2026-11-03):
    # questions go without a game and nothing asks again until one is detected.
    no_game = False
    pending_fired = None
    follow_up_wav: bytes | None = None
    follow_up_live: str | None = None  # the follow-up's words, heard live
    # The end-of-session recap (docs/player_notes.md, piece C): watches the
    # game's PROCESS, offers once when it has been gone for 20 s, and only
    # when this server has the turn ("recap" in auth_ok.features).
    recap = RecapTrigger()
    recap_due = False
    # A voice command with no game: the server hears it first, and only the morning
    # report is answered (`only_if_report`); anything else gets the game question.
    report_probe = False
    ask_game_now = False
    # The detected game that was in front when the player picked another one:
    # it doesn't take over again (a newly detected, different game does).
    pick_shadow: str | None = None

    while True:
        # A voice (and language) picked in the settings window while this
        # copy runs. Both waits below end as soon as one is saved, so it
        # applies within a poll of the window closing - not only after the
        # next question, which then still used the old language (live
        # report, 2026-10-04).
        saved_voice = load_saved_voice()
        if saved_voice != _voice:
            _set_voice(saved_voice)
            session.voice = _voice
            fillers = make_fillers()
            print(t("voice_changed", name=voices.get(_voice).name))
            _send_ui_info(ui_keys)
        # The player's own voice (Avançado): its fixed lines are made between turns,
        # and the fillers reload once they're there.
        own_voice.prewarm(_voice)
        lines_sig = own_voice.lines_signature(_voice)
        if lines_sig != own_lines_sig:
            own_lines_sig = lines_sig
            fillers = make_fillers()
        _sync_voice_listener(all_triggers)
        if _VOICE is not None and _VOICE.error:
            telemetry(f"  (comandos de voz indisponíveis: {_VOICE.error})")
            _VOICE.error = None

        if _GAME_PICKED.is_set():
            _GAME_PICKED.clear()
            picked = _PICKED_GAME["name"]
            if picked:
                shadow = detect_game(settings.game_aliases_path)
                pick_shadow = shadow if shadow != picked else None
                active_game, no_game, ask_game_now = picked, False, False
                _UI.game(picked)
                recap.forget()  # picked by hand: no matched window to watch for the recap
                print(t("game_picked", name=picked))
                print("-" * 48)
        detected, pick_shadow = _after_pick(detect_game(settings.game_aliases_path), pick_shadow)
        play_history.seen(detected)  # the play report counts what Ombri sees in front, any launcher
        if detected:
            if detected != active_game:
                _announce_game(settings, detected)
            active_game = detected
            no_game = False
            game = active_game
            proc = capture.last_game_process()
            if proc and proc[0] == detected:
                recap.seen(detected, proc[1], proc[2])
        elif active_game:
            game = active_game
        elif no_game:
            game = ""  # the wait below still leaves as soon as a game is detected
        else:
            if ask_game_now:
                ask_game_now = False
                found, fired = None, manager
            else:
                found, fired = _wait_for_game_or_trigger(
                    settings, all_triggers, stop=lambda: _voice_setting_changed() or _GAME_PICKED.is_set())
            if found:
                active_game = found
                game = found
                _announce_game(settings, found)
                proc = capture.last_game_process()
                if proc and proc[0] == found:
                    recap.seen(found, proc[1], proc[2])
            elif fired is None:
                continue  # the settings window changed the voice, or a game was picked: applied at the top
            elif fired is _CHAT:
                pending_fired = fired  # typed in the chat: answered without a game (below)
                game = ""
            elif _VOICE is not None and fired is _VOICE.guide:
                _guide_by_voice(_VOICE.guide.take_command())  # the pane moved (or closed); no game, no turn
                continue
            elif _VOICE is not None and fired is _VOICE.wake and session.supports("report_without_game"):
                report_probe = True
                pending_fired = fired
                game = ""
            else:
                resolved, is_freeform = resolve_active_game_by_voice(settings, session)
                if _GAME_PICKED.is_set():
                    print("-" * 48)
                    continue  # picked from the list while Ombri asked: the pick wins (top of the loop)
                if resolved == NO_GAME:
                    no_game = True
                    _say_no_game()
                    print("-" * 48)
                    continue
                if resolved:
                    active_game = resolved
                    game = resolved
                    _adopt_spoken_game(resolved, is_freeform, recap)
                else:
                    print(f"  {texts.spoken('game_unresolved')}")
                    play_line("game_unresolved", _voice)
                    print("-" * 48)
                    prompt_scene(all_triggers, t("retry_prompt"))
                    continue

        if recap_due:
            recap_due = False
            if _PAUSED.is_set():
                recap.skip()
            elif session.supports("recap"):
                pending_fired = _offer_recap(session, settings, recap.game or active_game, barge)

        def _foreground_game_changed() -> bool:
            # Also "changed": a new voice/language saved in the settings
            # window - the loop's top applies it before the next question.
            nonlocal recap_due
            if _voice_setting_changed() or _GAME_PICKED.is_set():
                return True
            seen_now = detect_game(settings.game_aliases_path)
            play_history.seen(seen_now)
            if seen_now and seen_now != pick_shadow:
                proc = capture.last_game_process()
                if proc and proc[0] == seen_now:
                    recap.seen(seen_now, proc[1], proc[2])
            if seen_now not in (None, active_game, pick_shadow):
                return True
            # The game's process, not its focus: alt-tabbing to Discord is not "gone".
            if recap.watching() and recap.tick(capture.game_process_alive(recap.pid, recap.exe)):
                recap_due = True
                return True  # leave the wait; the top of the loop makes the offer
            return False

        scene_prompt = t("scene_prompt", ask=_fmt(manager.hotkey), note=_fmt(remember_manager.hotkey))
        if _VOICE is not None and pending_fired is _VOICE.shush:
            pending_fired = None  # "chega": the answer stopped, nothing else starts
            print(t("stopped_by_voice"))
        if follow_up_wav is not None:
            scene, fired = "", manager  # answered like an F8 question, already recorded
            print(t("follow_up_heard"))
        elif pending_fired is not None:
            scene, fired = "", pending_fired
            pending_fired = None
            if fired is not _CHAT:
                print(t("interrupted_note" if fired is remember_manager else "interrupted_ask"))
        else:
            scene, fired = prompt_scene(
                all_triggers, scene_prompt,
                game_changed=_foreground_game_changed, game_poll_seconds=settings.game_detect_poll_seconds,
            )

        probe_turn, report_probe = report_probe, False
        if fired is _CHAT.mic:
            fired = manager  # the chat's mic button: exactly an F8 press
        typed_chat = False  # this question was typed in the PC chat: shown, not spoken
        if fired is _CHAT:
            message = _CHAT.take()
            if message is None:
                continue
            if _PAUSED.is_set():
                _UI.chat_done("cancelled")
                continue
            text, remember = message
            if remember:
                if not game:
                    _UI.chat_done("no_game")  # a note belongs to a game
                else:
                    _typed_note(session, game, text)
                print("-" * 48)
                continue
            scene, fired, typed_chat = text, None, True
        if fired is GAME_CHANGED:
            print("-" * 48)
            continue
        if _VOICE is not None and fired is _VOICE.shush:
            continue  # a stop word with nothing playing: nothing to stop
        if fired is not None and _PAUSED.is_set():
            follow_up_wav = follow_up_live = None
            continue  # paused from the tray: the key does nothing
        if _VOICE is not None and fired is _VOICE.guide:
            # "próxima" / "volta" / "repete" said alone with a build open
            # (listening mode on): next / back move the pane on the PC, no
            # turn; "repete" goes on below as a typed question, so Ombri says
            # this layer again (the server's fixed line, no recording).
            typed = _guide_by_voice(_VOICE.guide.take_command())
            if typed is None:
                continue
            scene, fired = typed, None

        if fired is None and not typed_chat and scene.strip().lower() == "jogo":
            new_game = _prompt(t("game_prompt"))
            if not new_game:
                print(t("bye"))
                session.close()
                return 0
            active_game = new_game
            recap.forget()  # typed name: no window to watch, no recap for it
            continue

        if fired is remember_manager:
            if not game:  # "no game" mode: a note belongs to a game, so ask which one
                resolved, is_freeform = resolve_active_game_by_voice(settings, session)
                if resolved == NO_GAME or not resolved:
                    if resolved == NO_GAME:
                        _say_no_game()
                    print("-" * 48)
                    continue
                active_game = game = resolved
                no_game = False
                _adopt_spoken_game(resolved, is_freeform, recap)
            pending_fired = _remember_note(session, settings, game, barge)
            print("-" * 48)
            continue

        used_trigger = fired is not None
        live_id = None  # this question's words, heard live (no WAV upload)
        from_wake = False
        heard_in_window = False  # this question came from the follow-up window, no key pressed
        question_text = None
        spoken_text = None  # the question heard by the player's own ears (sent as text)
        screenshot = None
        no_screen_reason = ""  # why there is no picture, told to the answer
        wav = None
        turn_id = uuid.uuid4().hex  # before the capture: the frame can travel ahead of the question under it
        early = None  # the frame sent at the first spoken word (client/net.py EarlyFrame), when it was
        if used_trigger:
            print(t("capturing"))
            screenshot, no_screen_reason = _capture_for_turn(settings)
            if screenshot is not None:
                _UI.chat_stage("screen")  # the chat's thinking row: "Olhando a tela"
            wake_audio = _VOICE.wake.take_audio() if _VOICE is not None and fired is _VOICE.wake else None
            from_wake = wake_audio is not None
            if fired is not None and _VOICE is not None and fired is _VOICE.wake:
                print(t("wake_heard"))
            if follow_up_wav is not None:
                wav, follow_up_wav = follow_up_wav, None
                live_id, follow_up_live = follow_up_live, None
                heard_in_window = True
            elif wake_audio is not None:
                wav = wake_audio  # "Ei Ombri, <question>": already recorded
                _UI.state("pensando")
            else:
                print(t("speak_question", hang=settings.mic_silence_hang))
                _UI.state("ouvindo")
                own_ears = own_voice.settings_for("ears")  # the player's own key: our STT never hears it
                live = None if own_ears else _live(session, settings)
                # The picture goes to the server at the first word (2026-11-26), so its
                # 4-5 s screen read runs while the player talks; the question then only
                # says the frame is there. A silent take sends nothing; a server that
                # doesn't know the message (None here) gets the frame with the question.
                early = session.early_frame(turn_id, game, screenshot)
                wav = _record(settings, live, on_speech=early.send if early is not None else None)
                live_id = _live_result(live, wav)
                _UI.state("pensando" if wav else "idle")
                if wav and own_ears:
                    heard = own_voice.own_transcribe(own_ears, wav, voices.get(_voice).language)
                    if heard is not None:  # None: the key failed - the WAV goes to Ombri's STT
                        spoken_text = heard
                        wav = None
                        if not heard:
                            _UI.state("idle")
                            print(t("empty_question"))
                            print("-" * 48)
                            continue
            if not wav and spoken_text is None:
                print(t("no_voice_type"))
                question_text = _prompt(t("question_prompt"))
        else:
            question_text = scene
        if spoken_text:
            question_text = spoken_text

        if not wav and not question_text:
            if early is not None:
                early.cancel()  # words were heard but nothing came of them: the server drops the read
            print(t("empty_question"))
            continue

        t_sent = time.perf_counter()  # recording done (silence hang included) -> request goes out
        t_first_heard: float | None = None
        cancel = threading.Event()
        audio_out = AudioOut()
        try:
            audio_out.open()
        except Exception as exc:  # noqa: BLE001
            print(t("no_audio_out", error=exc))
        if barge is not None:
            barge.arm(cancel, audio_out)
        # The player's own voice reads the answer (the server sends text only);
        # never for a typed question, which is shown, not spoken.
        own = None if typed_chat or not session.supports("text_only") else own_voice.settings_for("voice")
        speaker = own_voice.OwnSpeaker(own, audio_out, cancel,
                                       on_first=lambda: mark_answer_started(audio_out, cancel)) if own else None

        filler_timer = None
        search_timers: list = []
        answer_audio_started = False

        def on_state(state: str) -> None:
            nonlocal filler_timer
            telemetry(f"  [{state}]")
            if state in ("pensando", "pesquisando", "falando"):
                _UI.state(state)
            # "pensando" arrives after server-side STT + vision, right before
            # retrieval/Kimi - the same point the original app armed its
            # filler (the Kimi call), so FILLER_DELAY_MS means the same thing.
            if typed_chat:
                return  # answered in text: no filler, no "searching" line
            if (state == "pensando" and fillers is not None and filler_timer is None
                    and wait_line_allowed(WAIT_LINE_COOLDOWN)):
                filler_timer = fillers.arm(audio_out, cancel)
            # "pesquisando": no notes, the server is searching the web (3-10 s
            # of silence otherwise). Say so now - instead of a filler not yet
            # played - and once more if it drags on. Both give way to the
            # answer the moment it starts.
            elif state == "pesquisando" and not search_timers:
                if filler_timer is not None:
                    filler_timer.cancel()
                lines = (("web_search", 0.0),) if wait_line_allowed(WAIT_LINE_COOLDOWN) else ()
                for name, delay in (*lines, ("web_search_still", WEB_SEARCH_STILL_AFTER)):
                    pcm = load_line(name, _voice)
                    if pcm is not None:
                        search_timers.append(play_before_answer(audio_out, cancel, pcm, delay))

        answer_shown = False  # a turn_text piece reached the Chat view
        turn_cards: list = []  # links under the answer (turn_cards), shown after the answer text

        def on_meta(question: str, scene_text: str) -> None:
            print(t("you_asked", question=question))
            if scene_text:
                print(t("scene_detected", scene=scene_text))
            _UI.chat_question(question, game)  # the window's Chat view (memory only there)

        def on_text(piece: str) -> None:
            nonlocal answer_shown, t_first_heard
            answer_shown = True
            _UI.chat_answer(piece)
            if speaker is not None:
                if t_first_heard is None:
                    t_first_heard = time.perf_counter()
                speaker.say(piece)

        def on_cards(cards: list) -> None:
            # A "recipe" card (the brewing strip, server/brewing.py) comes right
            # after the question and goes to the window at once; the link cards
            # come just before the result and wait for the answer, as before.
            recipes = [c for c in cards if isinstance(c, dict) and c.get("kind") == "recipe"]
            links = [c for c in cards if not (isinstance(c, dict) and c.get("kind") == "recipe")]
            if links or not recipes:
                turn_cards[:] = links
            if recipes:
                _show_recipe(recipes[0])

        def on_audio(pcm: bytes, _turn_seq: int) -> None:
            nonlocal answer_audio_started, t_first_heard
            if not answer_audio_started:
                answer_audio_started = True
                t_first_heard = time.perf_counter()
                if not mark_answer_started(audio_out, cancel):
                    return
            audio_out.write(pcm, cancel)

        def record_reply() -> bytes | str | None:
            # F8 pressed for a note ("anota que...", "remind me that..."): the
            # server asks "save it?" inside this turn - record the yes/no.
            if typed_chat:  # typed: the chat's buttons answer
                _UI.chat_confirm(question_text or "")
                return _CHAT.wait_confirm(voices.get(_voice).language, cancel)
            print(t("answer_yes_no"))
            _UI.state("ouvindo")
            reply = _record(settings)
            _UI.state("pensando")
            return reply

        history_sent = play_history.snapshot()
        try:
            result = session.ask(
                turn_id, game, wav_bytes=None if live_id else wav, question_text=question_text,
                screenshot_bytes=screenshot, no_screen_reason=no_screen_reason, live_id=live_id,
                wake=from_wake, record_reply=record_reply if wav or typed_chat or spoken_text else None,
                follow_up=heard_in_window, only_if_report=probe_turn,
                text_only=(typed_chat or speaker is not None) and session.supports("text_only"),
                controls=gamepad.input_kind(from_pad=fired is gamepad) if gamepad.available else "keyboard",
                cancel=cancel, on_state=on_state, on_meta=on_meta, on_text=on_text, on_cards=on_cards, on_audio=on_audio,
                on_usage=_UI.usage, play_history=history_sent, play_history_unasked=play_history.unasked(),
                guide=_guide_field(), on_guide=_apply_guide_message,
                game_context=_game_context_field(game),
                visual=_visual_field(), on_visual=_apply_visual_message,
                early_frame=early,
            )
        except ServerError as exc:
            print(t("error", error=exc))
            _UI.state("idle")
            _UI.connection("offline")
            _UI.chat_done("failed")
            if speaker is not None:
                cancel.set()
                speaker.finish(timeout=2)
            for timer in [filler_timer, *search_timers]:
                if timer is not None:
                    timer.cancel()
            if barge is not None:
                barge.disarm()
            audio_out.close()
            print("-" * 48)
            continue
        play_history.after_turn(result, history_sent)
        if not answer_shown and result.get("answer_text"):
            # No streamed pieces (a fixed line: no notes, the closing, a cap):
            # the Chat view still gets the text that was spoken.
            _UI.chat_answer(str(result["answer_text"]))
            local = ("note_saved", "note_discarded", "ignored", "cap_reached", "closing")  # played from assets
            if settings.remember_hotkey.lower() == NO_NOTES_BAKED_HOTKEY:
                local += ("no_notes",)
            if speaker is not None and result.get("outcome") not in local:
                speaker.say(str(result["answer_text"]))
        if speaker is not None:
            speaker.finish()  # the own voice plays to the end (or the barge-in) before the turn closes
        if turn_cards:
            _UI.chat_cards(turn_cards)  # the Chat view checks each one again (http/https only)
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
        _UI.state("idle")
        _UI.connection("ok")

        outcome = result.get("outcome")
        _UI.chat_done(str(outcome or "failed"))
        if probe_turn:
            if outcome in ("need_game", "empty_question"):
                ask_game_now = True
                print("-" * 48)
                continue
            if outcome.startswith("answered"):
                no_game = True  # "qual é a missão?" gets an answer; a game opening ends this
        if outcome not in ("note_saved", "note_discarded", "ignored"):
            print(t("answer", text=result.get("answer_text", "")))
        elif outcome == "ignored":
            telemetry("  (janela de continuação: não era pra mim - ignorado)")
        if pending_fired is None:
            # Server sends no audio for these - the client speaks them from
            # shipped assets, interruptible: a trigger pressed while one
            # plays cuts it and becomes the next command (pending_fired).
            if outcome == "cap_reached":
                print(f"  {texts.spoken('cap_reached')}")
                pending_fired = _speak_line("cap_reached", barge)
            elif outcome == "answered_no_audio" and not typed_chat:
                print(t("no_quota_voice"))
                pending_fired = _speak_line("cap_reached", barge)
            elif outcome == "no_notes" and settings.remember_hotkey.lower() == NO_NOTES_BAKED_HOTKEY:
                pending_fired = _speak_line(NO_NOTES_LINE, barge)
            elif outcome == "note_saved":
                # F8 pressed for a note, and the player said "yes" (0.1.19)
                print(t("note_saved", text=result.get("answer_text", "")))
                play_confirm_tone()
                pending_fired = _speak_line("note_saved", barge, fallback_tone=False)
            elif outcome == "note_discarded":
                print(t("note_discarded", reason=result.get("stats", {}).get("reason", "")))
                play_stop_tone()
            elif outcome == "closing" and not heard_in_window:
                # A "valeu"/"interesting" heard by the follow-up window ends
                # the chat silently - the player was just reacting, not asking.
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
        follow_seconds = load_follow_up_seconds()
        if (follow_seconds > 0 and pending_fired is None and outcome in FOLLOW_UP_OUTCOMES
                and not typed_chat and not result.get("interrupted") and not _PAUSED.is_set()):
            follow_up_wav, pending_fired, follow_up_live = _listen_for_follow_up(
                settings, all_triggers, follow_seconds, session)
            if follow_up_wav is not None:
                telemetry("  (janela de continuação: fala ouvida)")
        print("-" * 48)


RECONNECT_SECONDS = 5.0


def _short_combo(combo: str) -> str:
    """"BACK + LEFTSHOULDER" -> "BACK+LB", for the window's key tile."""
    for long, short in (("LEFTSHOULDER", "LB"), ("RIGHTSHOULDER", "RB"), ("LEFTTRIGGER", "LT"),
                        ("RIGHTTRIGGER", "RT"), ("LEFTSTICK", "LS"), ("RIGHTSTICK", "RS")):
        combo = combo.replace(long, short)
    return combo.replace(" ", "")


def _voice_ui_label() -> str:
    v = voices.get(_voice)
    return f"{v.name} · {voices.language_name(v.language)}"


def _send_ui_info(keys: dict) -> None:
    _UI.info(voices.get(_voice).language, _voice_ui_label(), keys)


def _start_ui(settings) -> None:
    """Starts the desktop UI process (main window, tray, in-game orb). An
    installed copy started from the old console launcher (Parca.cmd) also
    hides that console once the window is up - the window replaces it."""
    global _UI
    _UI.close()
    _UI = HudClient(settings.overlay_enabled, log_path=ROOT / "output" / "overlay.log",
                    on_command=_on_ui_command, start_in_tray=_RELAUNCHED)
    capture.OWN_WINDOW_PIDS.clear()
    if _UI.pid:
        capture.OWN_WINDOW_PIDS.add(_UI.pid)  # its windows are blacked out of screenshots sent to Kimi
    _UI.connection("connecting")
    from client import speech as client_speech

    client_speech.level_hook = _UI.voice_level  # the main window's orb follows the voice
    from client.updater import INSTALL_MARKER

    if _UI.enabled and INSTALL_MARKER.exists() and os.name == "nt":
        try:
            import ctypes

            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
        except Exception:  # noqa: BLE001 - cosmetic
            pass


_DUCKER = None


def _start_ducking() -> None:
    """Lower the game while Ombri speaks (client/ducking.py). Any failure
    (no pycaw, no COM) just means the game keeps its volume."""
    global _DUCKER
    try:
        from client import speech as client_speech
        from client.ducking import GameDucker

        _DUCKER = GameDucker()
        _DUCKER.restore_leftover()  # a game left quiet by a crash gets its volume back
        client_speech.speaking_hook = _DUCKER.speaking
    except Exception as exc:  # noqa: BLE001
        print(f"(sem abaixar o jogo: {exc})")


def _restore_game_volume() -> None:
    if _DUCKER is not None:
        _DUCKER.restore_now()


def _on_ui_command(cmd: str) -> None:
    """A button in the UI process (tray menu, main window, notice). Runs on
    HudClient's reader thread."""
    if _CHAT.command(cmd):  # the PC chat: queued for the voice loop
        return
    if cmd == "quit":
        print(t("bye"))
        if _SESSION is not None:
            _SESSION.close()
        _restore_game_volume()
        _UI.close()
        os._exit(0)  # same abrupt exit Ctrl+C was; the voice loop has no gentler cancellation point
    elif cmd in ("pause", "resume"):
        if cmd == "pause":
            _PAUSED.set()
        else:
            _PAUSED.clear()
        _UI.paused(_PAUSED.is_set())
        print(t("paused" if _PAUSED.is_set() else "resumed"))
    elif cmd == "settings":
        # Not sent by the current UI (since 2026-11-21 the settings are a panel of
        # the main window, client/gui/settings_panel.py); kept for an older UI build.
        subprocess.Popen([sys.executable, "-m", "client.gui.token_dialog", "--reason", "settings"], cwd=ROOT)
    elif cmd == "relaunch_admin":
        _relaunch_as_admin()
    elif cmd == "restart_update":
        _restart_for_update()
    elif cmd in ("survey_open", "survey_later"):
        _answer_survey(cmd == "survey_open")
    elif cmd == "notes_list" or cmd.startswith("notes_delete:"):
        _notes_command(cmd)
    elif cmd.startswith("recap_save:"):
        _save_recap_card(cmd.removeprefix("recap_save:"))
    elif cmd.startswith("game_set:"):
        name = " ".join(cmd.removeprefix("game_set:").split())[:GAME_NAME_MAX]
        if name:
            _PICKED_GAME["name"] = name
            _GAME_PICKED.set()
    elif cmd == "games_list":
        _games_command()
    elif cmd.startswith(("guide_state:", "guide_list:", "guide_open:")):
        _guide_command(cmd)  # the Build pane (client/gui/build_pane.py)
    elif cmd.startswith(("pane_state:", "pane_hidden:")):
        _pane_command(cmd)  # the in-game pane (client/gui/game_pane.py)


def _after_pick(detected: str | None, pick_shadow: str | None) -> tuple[str | None, str | None]:
    """(detected, pick_shadow) once a game was picked by hand: the detected
    game that was in front at the pick is ignored, so the pick stays; any
    other detected game takes over and ends the shadow."""
    if detected and detected == pick_shadow:
        return None, pick_shadow
    if detected:
        return detected, None
    return None, pick_shadow


def _games_command() -> None:
    """The picker's list: GET /v1/games (curated games plus this tester's own)
    on its own thread; an empty list just leaves the picker typing-only."""
    session = _SESSION

    def work() -> None:
        try:
            games = session.list_games() if session is not None else []
        except ServerError as exc:
            telemetry(f"  (lista de jogos indisponível: {exc})")
            games = []
        _UI.games(games)

    threading.Thread(target=work, name="games-list", daemon=True).start()


def _save_recap_card(offer_id: str) -> None:
    """The card's "Sim, salva": REST on its own thread, so it never waits
    behind a question being answered on the WebSocket."""
    session = _SESSION

    def work() -> None:
        try:
            saved = session is not None and bool(offer_id) and session.save_recap_card(offer_id)
        except ServerError as exc:
            telemetry(f"  (resumo não salvo: {exc})")
            saved = False
        if saved:
            play_confirm_tone()
        _UI.notice("recap_saved" if saved else "recap_failed")

    threading.Thread(target=work, name="recap-save", daemon=True).start()


def _restart_for_update() -> None:
    """The update notice's button: start a fresh copy, which applies the
    update at startup (check_for_update) and then waits for this copy's
    single-instance lock (`--relaunched`), and step aside for it."""
    exe = windowed_python()
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen([exe, "-m", "client.main", "--relaunched"], cwd=ROOT, creationflags=flags,
                         close_fds=True)
    except OSError as exc:
        print(f"(não consegui reiniciar: {exc})")
        return
    if _SESSION is not None:
        _SESSION.close()
    _restore_game_volume()
    _UI.close()
    os._exit(0)


# How often a running app asks whether a newer version exists (Ombri
# usually stays open in the tray for days; it only updates when it starts).
UPDATE_CHECK_SECONDS = float(os.environ.get("UPDATE_CHECK_SECONDS", "") or 3 * 3600)


def _watch_for_updates() -> None:
    """Daemon thread: every UPDATE_CHECK_SECONDS, if a newer version is out,
    show the "new version - restart?" notice. Repeats each check until the
    tester restarts; silent on any failure."""
    from client.updater import available_update

    while True:
        time.sleep(UPDATE_CHECK_SECONDS)
        version = available_update()
        if version:
            print(f"(nova versão disponível: {version})")
            _UI.notice("update", version=version)


ANNOUNCE_SEEN_PATH = None  # set lazily: %APPDATA%/Parca/announcements_seen.json
SURVEY_MAX_SHOWS = 3  # a notice that times out unanswered comes back on a later start, up to this
# The personality test has clips in these languages only (server/personality.py):
# a Spanish voice is not invited (it would get the Portuguese page).
SURVEY_LANGUAGES = ("pt", "en")
_SURVEY: dict = {}  # the invite shown this run: {"id", "url"}


def _survey_log():
    from client.config import USER_SETTINGS_DIR

    return USER_SETTINGS_DIR / "surveys.json"


def _survey_state() -> dict:
    try:
        data = json.loads(_survey_log().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_survey_state(state: dict) -> None:
    try:
        path = _survey_log()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def _survey_url(server_url: str, code: str) -> str | None:
    """https://<the server this app talks to>/ouvir/?l=pt#<code> - only for our
    own hosts, whatever the server sent, and only in a language the test has."""
    from urllib.parse import quote, urlsplit

    host = urlsplit(server_url).hostname or ""
    if host not in ("api.ombriplay.com", "api.parcaplay.com"):
        return None
    lang = texts.language()
    if lang not in SURVEY_LANGUAGES:
        return None
    return f"https://{host}/ouvir/?l={lang}#{quote(code, safe='.')}"


def _show_survey(session) -> None:
    """The personality test invite (auth_ok "survey", 0.1.20+): "Quero
    participar" opens the test page in the browser. Answered (either button) =
    never again; ignored until it hides = shown again on a later start, at most
    SURVEY_MAX_SHOWS times."""
    survey = getattr(session, "survey", None)
    if not survey:
        return
    state = _survey_state()
    entry = state.get(survey["id"]) or {}
    if entry.get("answered") or entry.get("shows", 0) >= SURVEY_MAX_SHOWS:
        return
    url = _survey_url(session.server_url, str(survey["code"]))
    if not url:
        return
    _SURVEY.update(id=survey["id"], url=url)
    state[survey["id"]] = {"shows": entry.get("shows", 0) + 1}
    _save_survey_state(state)
    _UI.notice("survey")


def _answer_survey(join: bool) -> None:
    if not _SURVEY:
        return
    state = _survey_state()
    state[_SURVEY["id"]] = {**(state.get(_SURVEY["id"]) or {}), "answered": "open" if join else "later"}
    _save_survey_state(state)
    if join:
        import webbrowser

        webbrowser.open(_SURVEY["url"])


def _show_announcement(announce: dict | None) -> None:
    """The server's current announcement (auth_ok), once per id."""
    if not announce:
        return
    from client.config import USER_SETTINGS_DIR

    path = USER_SETTINGS_DIR / "announcements_seen.json"
    try:
        seen = set(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        seen = set()
    if announce["id"] in seen:
        return
    text = announce.get(texts.language()) or announce.get("pt") or ""
    if not text:
        return
    _UI.notice("announce", text=text)
    seen.add(announce["id"])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(seen)), encoding="utf-8")
    except OSError:
        pass


def _relaunch_as_admin() -> None:
    """The admin notice's button: start an elevated copy (Windows asks for
    permission) and step aside for it - it waits for this copy's
    single-instance lock (see `--relaunched`)."""
    if os.name != "nt":
        return
    import ctypes

    exe = windowed_python()
    ok = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, "-m client.main --relaunched", str(ROOT), 1)
    if ok > 32:  # <= 32 is an error, including the user saying no to the prompt
        if _SESSION is not None:
            _SESSION.close()
        _restore_game_volume()
        _UI.close()
        os._exit(0)


_INSTANCE_MUTEX = None  # held for the whole process lifetime


def _claim_single_instance(wait_seconds: float = 0.0) -> bool:
    """See _claim_once(). `wait_seconds` > 0 (an elevated relaunch) keeps
    trying while the copy that launched it exits."""
    deadline = time.monotonic() + wait_seconds
    while True:
        if _claim_once():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def _claim_once() -> bool:
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
        # The handle to the OTHER copy's mutex must be closed: an open handle
        # keeps the named mutex alive, so a copy retrying for the lock (an
        # elevated relaunch) would otherwise hold it forever itself.
        k32.CloseHandle(ctypes.c_void_p(handle))
        return False
    _INSTANCE_MUTEX = handle
    return True


SETTINGS_SHORTCUT_NAME = "Parça - Configurações.lnk"  # v0.1.6-v0.1.7 only; removed by _tidy_shortcuts()
OLD_LAUNCH_SHORTCUT_NAME = "Parça.lnk"  # before the Ombri rename; replaced by _tidy_shortcuts()
LAUNCH_SHORTCUT_NAME = "Ombri.lnk"
# A new file name, not parca.ico: Windows caches a shortcut's icon by path, so installs that
# had the sphere in parca.ico kept showing it in the taskbar after the update (owner, 2026-11-23).
ICON_PATH = ROOT / "client" / "assets" / "ombri.ico"


def _log_without_console() -> None:
    """Started by pythonw.exe (the desktop icon since 2026-09-29) there is
    no console: print() would go nowhere and any direct write to stdout
    would raise. Everything goes to output/parca.log instead (the previous
    run's kept as parca.prev.log). The copy started after an update finds
    parca.log still open in the updating parent, which waits for it: Windows
    refuses the rename, so it appends to the parent's file (until 0.1.19 it
    fell to devnull and the whole session after an update went unlogged).
    Always append mode: two copies writing one file each land at its end."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log = ROOT / "output" / "parca.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        if log.exists():
            try:
                log.replace(log.with_name("parca.prev.log"))
            except OSError:
                pass
        stream = open(log, "a", encoding="utf-8", buffering=1)
    except OSError:
        stream = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stdout or stream
    sys.stderr = sys.stderr or stream


def _tidy_shortcuts() -> None:
    """Once per installed PC: the app's icons carry its current name. Since
    the Parça -> Ombri rename (2026-10) the "Parça" Start menu entry becomes
    "Ombri", and a desktop "Parça" icon becomes "Ombri" too - only if the
    tester has one (the installer asks whether to create it; a tester who
    said no, or deleted it, doesn't get it back). The old "Parça -
    Configurações" icon (v0.1.6-v0.1.7) is removed. Both installs are
    covered: Parca-Setup.exe's runtime/ and instalar-parca.cmd's .venv.
    They launch pythonw.exe (no console) with the orb icon - or, once
    runtime/Ombri.exe is in place (client.launcher), that one, so Task
    Manager says Ombri: a second pass (shortcuts_v5) repoints them. The v6
    pass points them at ombri.ico (the mascot's head; the cached parca.ico was
    still the sphere) and asks Windows to redraw its icons. Best-effort and
    silent; recorded in settings.json."""
    from client.config import _read_user_settings, save_user_settings
    from client.updater import INSTALL_MARKER, REPO_ROOT

    pythonw = next((p for p in (REPO_ROOT / "runtime" / "pythonw.exe",
                                REPO_ROOT / ".venv" / "Scripts" / "pythonw.exe") if p.exists()), None)
    launcher = install_launcher() if pythonw is not None else None
    done_key = "shortcuts_v6" if launcher else "shortcuts_v6_py"
    if not INSTALL_MARKER.exists() or pythonw is None or _read_user_settings().get(done_key):
        return
    script = (
        "$w = New-Object -ComObject WScript.Shell; "
        "$desk = [Environment]::GetFolderPath('Desktop'); $menu = [Environment]::GetFolderPath('Programs'); "
        "$set = Join-Path $desk $env:PARCA_SET_LNK; if (Test-Path $set) { Remove-Item $set }; "
        "$targets = @((Join-Path $menu $env:PARCA_LNK)); "
        "if ((Test-Path (Join-Path $desk $env:PARCA_OLD_LNK)) -or (Test-Path (Join-Path $desk $env:PARCA_LNK))) "
        "{ $targets += (Join-Path $desk $env:PARCA_LNK) }; "
        "foreach ($t in $targets) { $s = $w.CreateShortcut($t); $s.TargetPath = $env:PARCA_PYW; "
        "$s.Arguments = '-m client.main'; $s.WorkingDirectory = $env:PARCA_HOME; "
        "if (Test-Path $env:PARCA_ICO) { $s.IconLocation = $env:PARCA_ICO }; $s.Save() }; "
        "foreach ($d in @($desk, $menu)) { $o = Join-Path $d $env:PARCA_OLD_LNK; if (Test-Path $o) { Remove-Item $o } }"
    )
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            env=dict(os.environ, PARCA_LNK=LAUNCH_SHORTCUT_NAME, PARCA_OLD_LNK=OLD_LAUNCH_SHORTCUT_NAME,
                     PARCA_SET_LNK=SETTINGS_SHORTCUT_NAME, PARCA_PYW=str(launcher or pythonw), PARCA_HOME=str(REPO_ROOT),
                     PARCA_ICO=str(ICON_PATH)),
            capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).returncode == 0
    except Exception:  # noqa: BLE001
        done = False
    if done:
        try:  # SHCNE_ASSOCCHANGED: the taskbar and Start menu drop their cached icons
            import ctypes
            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
        except (OSError, AttributeError):
            pass
        save_user_settings(**{done_key: True})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m client.main")
    ap.add_argument("--settings", action="store_true",
                    help="open the settings window (language, voice, token) and exit")
    ap.add_argument("--relaunched", action="store_true", help=argparse.SUPPRESS)  # elevated restart
    args = ap.parse_args(argv)
    if relaunch_as_ombri(sys.argv[1:] if argv is None else argv):
        return 0  # that copy runs as Ombri.exe; before the log, so it keeps parca.log
    _log_without_console()
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
    global _RELAUNCHED
    _RELAUNCHED = bool(args.relaunched)
    if not _claim_single_instance(10.0 if args.relaunched else 0.0):
        _set_voice(load_saved_voice())
        print(texts.spoken("already_running"))
        play_line("already_running", _voice, fallback_tone=False)
        return 1
    _tidy_shortcuts()
    _start_ducking()
    try:
        return cmd_ask(args)
    except KeyboardInterrupt:
        print("\n" + t("bye"))
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
