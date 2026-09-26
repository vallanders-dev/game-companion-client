"""client/net.py — ServerSession: the client's side of the real WebSocket
ask-turn protocol (the approved plan's `server/`, mirrored client-side).

One persistent connection per client run (plan decision 3): `connect()`
opens it and performs the auth handshake once; `ask()` can then be called
repeatedly, one call per F8 turn, over that same connection. Uses
`websockets.sync.client` - the same synchronous WebSocket client
`server/speech.py`'s `TtsStream` already depends on (already a project
dependency, not a new one), matching this whole client's synchronous/
threaded style (no asyncio anywhere client-side - that stays a
server-only concern, per `server/app.py`'s own docstring on why it needs
an event loop and this side doesn't).

Wire format, exactly `server/protocol.py`'s: JSON text frames for control
messages, binary frames for PNG/WAV payloads (client -> server) and
`turn_seq`-prefixed PCM (server -> client, see `unpack_audio_frame()`
duplicated here rather than imported - this package must never import
anything from `server/`, the whole point of the split).
"""
from __future__ import annotations

import json
import struct
import threading


class ServerError(RuntimeError):
    """Raised on an `auth_error` handshake response, or a closed/broken
    connection encountered mid-call."""


def _unpack_audio_frame(frame: bytes) -> tuple[int, bytes]:
    return struct.unpack(">I", frame[:4])[0], frame[4:]


class ServerSession:
    def __init__(self, server_url: str, token: str) -> None:
        self.server_url = server_url
        self.token = token
        self.display_name = ""
        self._ws = None

    # -- lifecycle ------------------------------------------------------------
    def connect(self, timeout: float = 15.0) -> str:
        """Opens the WebSocket, performs the auth handshake, returns the
        tester's display_name. Raises ServerError on `auth_error` or a
        connection failure - the caller decides whether that's fatal."""
        from websockets.sync.client import connect

        self.close()
        try:
            self._ws = connect(
                self.server_url, additional_headers={"Authorization": f"Bearer {self.token}"},
                open_timeout=timeout,
            )
            raw = self._ws.recv(timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - connection/handshake failure, not our business which kind
            self.close()
            raise ServerError(f"não consegui conectar ao servidor: {exc}") from exc
        msg = json.loads(raw)
        if msg.get("type") == "auth_error":
            self.close()
            raise ServerError(f"servidor recusou o token: {msg.get('detail', '')}")
        if msg.get("type") != "auth_ok":
            self.close()
            raise ServerError(f"handshake inesperado do servidor: {msg}")
        self.display_name = str(msg.get("display_name", ""))
        return self.display_name

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
            self._ws = None

    @property
    def is_connected(self) -> bool:
        return self._ws is not None

    def _send_opening(self, frames: list) -> None:
        """Sends the opening frames of a request (turn_start / stt_request
        + its binary frames). Reconnects first if the connection is known to
        be gone, and if the send itself fails - the idle connection was
        closed under us by a server restart/deploy or a network blip - it
        reconnects and resends ONCE: nothing of this request reached the
        server, so a resend can't double anything. A connection that dies
        mid-answer is NOT retried (the server may already have done paid
        work); that one turn fails, and the next request reconnects here."""
        for attempt in (1, 2):
            if self._ws is None:
                self.connect()
            try:
                for frame in frames:
                    self._ws.send(frame)
                return
            except Exception as exc:  # noqa: BLE001 - any send failure means a dead connection
                self.close()
                if attempt == 2:
                    raise ServerError(f"conexão com o servidor perdida: {exc}") from exc

    # -- ask turn ---------------------------------------------------------------
    def ask(
        self,
        turn_id: str,
        game: str,
        *,
        wav_bytes: bytes | None = None,
        question_text: str | None = None,
        screenshot_bytes: bytes | None = None,
        allow_spoilers: bool | None = None,
        cancel: threading.Event | None = None,
        on_state=None,
        on_meta=None,
        on_text=None,
        on_audio=None,
        on_usage=None,
    ) -> dict:
        """One ask-turn, start to finish - blocks until `turn_result`
        arrives (or the connection breaks). `wav_bytes` is the real path
        (server-side Scribe STT); `question_text` is for a typed question
        (this app's own "digite a pergunta" fallback has always existed -
        there is no reason to round-trip already-known text through STT,
        this isn't just a test convenience here). Exactly one of the two
        should be given.

        `cancel`: a `threading.Event` the caller's OWN barge-in watcher
        (a separate thread polling the trigger, same design as the
        single-process app's `BargeInWatcher`) can set to fire
        `turn_cancel` - checked between reads, at most one poll-interval
        of latency, which is fine (this message is documented as
        latency-tolerant; the real, sub-100ms interrupt is the caller's
        own local `AudioOut.interrupt()`, called directly by the barge-in
        watcher, not through this method at all).

        Callbacks fire synchronously, on the calling thread, as each
        message arrives - `on_audio(pcm, turn_seq)` is where the caller
        writes to its own `AudioOut`.
        """
        msg: dict = {
            "type": "turn_start", "turn_id": turn_id, "kind": "ask", "game": game,
            "has_screenshot": screenshot_bytes is not None, "has_audio": wav_bytes is not None,
        }
        if question_text is not None:
            msg["question_text"] = question_text
        if allow_spoilers is not None:
            msg["allow_spoilers"] = allow_spoilers
        frames: list = [json.dumps(msg)]
        if screenshot_bytes is not None:
            frames.append(screenshot_bytes)
        if wav_bytes is not None:
            frames.append(wav_bytes)
        self._send_opening(frames)

        return self._pump(turn_id, cancel, on_state, on_meta, on_text, on_audio, on_usage)

    def _pump(self, turn_id: str, cancel, on_state, on_meta, on_text, on_audio, on_usage) -> dict:
        cancel_sent = False
        while True:
            if cancel is not None and cancel.is_set() and not cancel_sent:
                cancel_sent = True
                try:
                    self._ws.send(json.dumps({"type": "turn_cancel", "turn_id": turn_id}))
                except Exception:  # noqa: BLE001 - dead connection: the recv below reports it
                    pass
            try:
                raw = self._ws.recv(timeout=0.05)
            except TimeoutError:
                continue
            except Exception as exc:  # noqa: BLE001 - connection dropped mid-turn
                self.close()  # the next request reconnects
                raise ServerError(f"conexão caiu durante o turno: {exc}") from exc

            if isinstance(raw, (bytes, bytearray)):
                if on_audio is not None:
                    turn_seq, pcm = _unpack_audio_frame(raw)
                    on_audio(pcm, turn_seq)
                continue

            data = json.loads(raw)
            mtype = data.get("type")
            if mtype == "turn_state" and on_state is not None:
                on_state(data["state"])
            elif mtype == "turn_meta" and on_meta is not None:
                on_meta(data["question"], data["scene"])
            elif mtype == "turn_text" and on_text is not None:
                on_text(data["text"])
            elif mtype == "usage_update" and on_usage is not None:
                on_usage(data["count"], data["cap"])
            elif mtype == "turn_result":
                return data
            # anything else (a stray note_prompt/stt_result meant for a
            # different flow): ignored here - this is the ask-turn pump only.

    # -- F6 remember-note (multi-round) --------------------------------------
    def remember(
        self,
        turn_id: str,
        game: str,
        note_wav: bytes,
        *,
        screenshot_bytes: bytes | None = None,
        record_reply,
        cancel: threading.Event | None = None,
        on_state=None,
        on_audio=None,
        on_usage=None,
    ) -> dict:
        """The F6 flow: send the dictated note, then answer the server's
        confirm-ask (and, once, its re-ask) by RECORDING a fresh reply each
        time - `record_reply()` is a zero-arg callable the caller supplies
        (its own mic-recording primitive) that returns WAV bytes or `None`
        for a genuine empty capture. Called once per `note_prompt`, at most
        twice (the server's own one-re-ask rule).

        The protocol has no dedicated "prompt audio finished" message -
        `on_state("ouvindo")` IS that signal (see `server/session.py`'s
        `run_remember_turn()`: it pushes exactly this state right after
        sending the prompt's audio, specifically so a real client knows
        when to stop playing and start listening) - `record_reply()` is
        called at that point, not merely on seeing `note_prompt` itself
        (which arrives before the prompt's own audio has even finished
        streaming).
        """
        msg = {
            "type": "turn_start", "turn_id": turn_id, "kind": "remember", "game": game,
            "has_screenshot": screenshot_bytes is not None, "has_audio": True,
        }
        frames: list = [json.dumps(msg)]
        if screenshot_bytes is not None:
            frames.append(screenshot_bytes)
        frames.append(note_wav)
        self._send_opening(frames)

        cancel_sent = False
        while True:
            if cancel is not None and cancel.is_set() and not cancel_sent:
                cancel_sent = True
                try:
                    self._ws.send(json.dumps({"type": "turn_cancel", "turn_id": turn_id}))
                except Exception:  # noqa: BLE001 - dead connection: the recv below reports it
                    pass
            try:
                raw = self._ws.recv(timeout=0.05)
            except TimeoutError:
                continue
            except Exception as exc:  # noqa: BLE001
                self.close()  # the next request reconnects
                raise ServerError(f"conexão caiu durante o turno: {exc}") from exc

            if isinstance(raw, (bytes, bytearray)):
                if on_audio is not None:
                    turn_seq, pcm = _unpack_audio_frame(raw)
                    on_audio(pcm, turn_seq)
                continue

            data = json.loads(raw)
            mtype = data.get("type")
            if mtype == "turn_state":
                if on_state is not None:
                    on_state(data["state"])
                if data["state"] == "ouvindo":
                    reply_wav = record_reply()
                    try:
                        self._ws.send(json.dumps({
                            "type": "turn_continue", "turn_id": turn_id, "has_audio": bool(reply_wav),
                        }))
                        if reply_wav:
                            self._ws.send(reply_wav)
                    except Exception as exc:  # noqa: BLE001
                        self.close()
                        raise ServerError(f"conexão caiu durante o turno: {exc}") from exc
            elif mtype == "usage_update" and on_usage is not None:
                on_usage(data["count"], data["cap"])
            elif mtype == "turn_result":
                return data
            # "note_prompt" itself carries no actionable info beyond what
            # `on_state("ouvindo")` already triggers off of - not handled
            # separately here (a caller wanting the note_text/reask flag to
            # show on an overlay can still watch for it via a lower-level
            # loop later; this method keeps the common path simple).

    # -- voice game-detection fallback -------------------------------------
    def stt_request(self, wav_bytes: bytes, timeout: float = 30.0) -> tuple[str, str | None]:
        """The voice game-detection fallback's one-shot RPC (see the
        approved plan and `server/session.py`'s `run_stt_request()`) -
        transcribe `wav_bytes`, nothing else. `capture.resolve_spoken_game()`
        does the actual matching against known games, entirely locally,
        exactly as it already does in the single-process app - this
        method only replaces the local Scribe call with a network one.
        Returns `(text, error)`, same contract as the server function."""
        request_id = f"stt-{id(wav_bytes)}-{threading.get_ident()}"
        self._send_opening([json.dumps({"type": "stt_request", "request_id": request_id, "has_audio": True}),
                            wav_bytes])
        while True:
            try:
                raw = self._ws.recv(timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                self.close()  # the next request reconnects
                raise ServerError(f"conexão caiu durante a transcrição: {exc}") from exc
            if isinstance(raw, (bytes, bytearray)):
                continue  # not expected here, ignore defensively
            data = json.loads(raw)
            if data.get("type") == "stt_result" and data.get("request_id") == request_id:
                return data.get("text", ""), data.get("error")
