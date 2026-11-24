"""client/gui/settings_save.py — the ONE save path for Ombri's settings.

Two windows edit the same file (%APPDATA%/Parca/settings.json, written by
client.config.save_user_settings): the first-launch window
(client/gui/token_dialog.py, a token that is missing or was rejected) and
the Configurações panel inside the main window (client/gui/settings_panel.py,
2026-11-21 - until then the panel's job was the same dialog, run as its own
process with `--reason settings`). Both call `submit()` here, so a token is
checked, kept or saved by one piece of code, and the two never drift apart.

The rule, unchanged from the dialog:
  * token empty and `keep_saved` (settings: a token is already saved):
    voice and options are written at once, the saved token stays;
  * token empty otherwise: nothing is written ("paste the token first");
  * a token: checked against the server on a worker thread
    (ServerSession.connect, 15 s); written - with the voice and options -
    ONLY when the server accepts it. `done(kind, name)` is then called on
    that worker thread, kind "ok" | "rejected" | "unreachable", AFTER the
    write on "ok"; the caller moves back to its own GUI thread (both windows
    poll a result slot with a QTimer, as the dialog always did).

No Qt here: this is plain Python so the harness can call it directly.
"""
from __future__ import annotations

import re
import threading
from collections.abc import Callable
from typing import NamedTuple

from client.config import save_user_settings
from client.net import AuthError, ServerSession

CHECK_TIMEOUT = 15.0


def clean_token(raw: str) -> str:
    """Accepts the token alone or the whole `SERVER_AUTH_TOKEN=...` line from
    the invite message, and drops spaces/line breaks picked up by the paste."""
    text = raw.strip()
    text = re.sub(r"^\s*SERVER_AUTH_TOKEN\s*=\s*", "", text)
    return re.sub(r"\s+", "", text)


class Outcome(NamedTuple):
    status: str  # "saved" | "need_token" | "checking"
    worker: threading.Thread | None  # the token check, while "checking"


def submit(raw_token: str, *, voice: str, fields: dict, keep_saved: bool, server_url: str,
           done: Callable[[str, str], None]) -> Outcome:
    """Saves the settings the way the token window always did; returns what
    happened so the window can say it:
      "saved"      - written now (no token typed, the saved one stays);
      "need_token" - nothing written, there is no token to keep;
      "checking"   - `worker` is checking the token; `done` follows.
    `fields` are the option values (duck_game, duck_level, show_in_recordings,
    follow_up, voice_commands, play_history when changed), already read from
    the widgets by the caller."""
    token = clean_token(raw_token)
    if not token and keep_saved:
        # Only language/voice/options changed: nothing to check with the server.
        save_user_settings(voice=voice, **fields)
        return Outcome("saved", None)
    if not token:
        return Outcome("need_token", None)

    def check() -> None:
        session = ServerSession(server_url, token)
        kind, name = "unreachable", ""
        try:
            name = session.connect(timeout=CHECK_TIMEOUT)
            kind = "ok"
        except AuthError:
            kind = "rejected"
        except Exception:  # noqa: BLE001 - ServerError (no server), or a handshake we can't read: same answer
            kind = "unreachable"
        finally:
            session.close()
        if kind == "ok":
            save_user_settings(token=token, voice=voice, **fields)
        done(kind, name)

    worker = threading.Thread(target=check, name="settings-token-check", daemon=True)
    worker.start()
    return Outcome("checking", worker)
