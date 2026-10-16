"""client/config.py — client-side settings (the real client/ package,
client/server split).

Split off the root `config.py` `Settings` dataclass per the approved
architecture plan's settings-split table: everything local-hardware
(hotkeys, mic tuning, the overlay/tray, game detection) lives here, with
NO paid API keys and NO knowledge-base path at all - this process never
talks to Moonshot/ElevenLabs directly, only to the server over
`server_url`. Deliberately NOT importing from root `config.py` - that
module still backs the current single-process app on `main` and stays
there; some `DEFAULT_*` values are duplicated between the two for the
duration of this migration (see `server/config.py`'s identical note).

Two layers, same shape as root `config.py`:
  * ``.env`` (or the process environment) - `SERVER_URL` and
    `SERVER_AUTH_TOKEN`, the only two secrets/settings this process has
    that a curious user could misuse (and even those are just "which
    server, which tester" - never a Moonshot/ElevenLabs key).
  * ``config.json`` - the user-editable hotkey rebind, same file and
    format the current single-process app already uses.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent  # client/, not the repo root
USER_CONFIG_PATH = PROJECT_ROOT / "config.json"

# The hosted Ombri server. client/.env's SERVER_URL overrides it (local
# development against `python -m server.main serve`). The same server still
# answers at the pre-rename Parça address, the fallback if this one can't be
# reached (client.net.ServerSession).
DEFAULT_SERVER_URL = "wss://api.ombriplay.com/v1/ws"
FALLBACK_SERVER_URLS = ("wss://api.parcaplay.com/v1/ws",)

# Where a tester's token lives once entered in the first-launch window
# (client/gui/token_dialog.py): their own Windows profile, outside the
# program folder, so it's never copied, zipped or committed with the app.
USER_SETTINGS_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "Parca"
USER_SETTINGS_PATH = USER_SETTINGS_DIR / "settings.json"
DEFAULT_CAPTURE_HOTKEY = "f8"
DEFAULT_REMEMBER_HOTKEY = "f6"
DEFAULT_GAMEPAD_COMBO = "back+leftshoulder"
DEFAULT_MIC_SILENCE_HANG = 2.5
DEFAULT_MIC_MAX_DURATION = 25.0
DEFAULT_MIC_MIN_DURATION = 0.5
DEFAULT_MIC_VAD_THRESHOLD = 0.5
DEFAULT_MIC_SPEECH_PAD_MS = 30.0
DEFAULT_MIC_NO_SPEECH_TIMEOUT = 4.0
DEFAULT_MIN_SPEECH_SECONDS = 1.2
DEFAULT_BARGE_IN = True
# Filler ("Deixa eu ver...") if the answer audio hasn't started this long
# after the server reports "pensando" (right before retrieval/Kimi) - the
# same starting point and default as the original app's FILLER_DELAY_MS.
DEFAULT_FILLER_DELAY_MS = 500.0
DEFAULT_FILLER_ENABLED = True
# The whole desktop UI (main window, tray, in-game orb - client/gui/ui_process.py).
# On since stage 1 of the desktop UI (2026-09-29); OVERLAY=false = console only.
DEFAULT_OVERLAY_ENABLED = True
DEFAULT_VERBOSE_TELEMETRY = True
DEFAULT_GAME_DETECT_POLL_SECONDS = 1.0
DEFAULT_GAME_VOICE_MATCH_MIN_RATIO = 0.72


@dataclass
class ClientSettings:
    server_url: str
    server_auth_token: str
    capture_hotkey: str
    remember_hotkey: str
    gamepad_combo: str
    mic_silence_hang: float
    mic_max_duration: float
    mic_min_duration: float
    mic_vad_threshold: float
    mic_speech_pad_ms: float
    mic_no_speech_timeout: float
    min_speech_seconds: float
    game_aliases_path: Path
    output_dir: Path
    barge_in: bool
    filler_delay_ms: float
    filler_enabled: bool
    overlay_enabled: bool
    verbose_telemetry: bool
    game_detect_poll_seconds: float
    game_voice_match_min_ratio: float
    # client/voices.py key: the voice AND the conversation's language.
    voice: str = "raquel"
    # End-of-turn model (client/turn_detect.py): stop listening when the
    # sentence is finished instead of after mic_silence_hang. SMART_TURN=false = the fixed wait.
    smart_turn: bool = True


def load_user_config() -> dict:
    try:
        with open(USER_CONFIG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Aviso: não consegui ler {USER_CONFIG_PATH.name} ({exc}); ignorando.")
        return {}
    return data if isinstance(data, dict) else {}


def save_user_config(data: dict) -> None:
    with open(USER_CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def _env(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def _path(env_name: str, default: Path) -> Path:
    raw = os.environ.get(env_name, "").strip()
    return Path(raw).expanduser() if raw else default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on", "enabled")


def resolve_capture_hotkey(user_cfg: dict | None = None) -> str:
    """config.json -> CAPTURE_HOTKEY env var -> built-in default. Same
    precedence as root config.py's identical function."""
    cfg = load_user_config() if user_cfg is None else user_cfg
    from_cfg = str(cfg.get("capture_hotkey", "")).strip()
    return from_cfg or os.environ.get("CAPTURE_HOTKEY", "").strip() or DEFAULT_CAPTURE_HOTKEY


def resolve_remember_hotkey(user_cfg: dict | None = None) -> str:
    cfg = load_user_config() if user_cfg is None else user_cfg
    from_cfg = str(cfg.get("remember_hotkey", "")).strip()
    return from_cfg or os.environ.get("REMEMBER_HOTKEY", "").strip() or DEFAULT_REMEMBER_HOTKEY


def resolve_gamepad_combo(user_cfg: dict | None = None) -> str:
    cfg = load_user_config() if user_cfg is None else user_cfg
    from_cfg = str(cfg.get("gamepad_combo", "")).strip()
    return from_cfg or os.environ.get("GAMEPAD_COMBO", "").strip() or DEFAULT_GAMEPAD_COMBO


def _read_user_settings() -> dict:
    try:
        data = json.loads(USER_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load_saved_token() -> str:
    return str(_read_user_settings().get("token", "")).strip()


def load_saved_voice() -> str:
    """The voice picked in the settings window (client/voices.py key); the
    default voice when none was ever picked. VOICE in client/.env overrides
    it for development."""
    from client import voices

    return voices.get(os.environ.get("VOICE", "").strip() or _read_user_settings().get("voice")).key


DEFAULT_DUCK_LEVEL = 0.3  # the game at 30% of its own volume while Ombri speaks


def load_duck_settings() -> tuple[bool, float]:
    """(enabled, level) for client/ducking.py, read fresh each time so a
    change in the settings window applies to the next answer. DUCK_GAME=false
    in client/.env turns it off."""
    data = _read_user_settings()
    if os.environ.get("DUCK_GAME", "").strip().lower() in ("0", "false", "no", "off"):
        return False, DEFAULT_DUCK_LEVEL
    try:
        level = float(data.get("duck_level", DEFAULT_DUCK_LEVEL))
    except (TypeError, ValueError):
        level = DEFAULT_DUCK_LEVEL
    return bool(data.get("duck_game", True)), min(0.9, max(0.05, level))


def load_show_in_recordings() -> bool:
    """Whether the in-game orb shows up in OBS / Game Bar / Discord captures
    (client/gui/overlay.py). Default on: demos and streams should show Ombri."""
    return bool(_read_user_settings().get("show_in_recordings", True))


# The follow-up window (2026-10-14): after an answer the mic stays open this
# long, with no beep, so a follow-up needs no F8. It ends as soon as nothing
# is said in time, a key is pressed, or the player says "valeu" (the closing
# gate ends the conversation - no window after it).
DEFAULT_FOLLOW_UP_SECONDS = 5.0


def load_follow_up_seconds() -> float:
    """Seconds the follow-up window stays open; 0 = off. Read fresh each time
    so the settings window applies to the next answer. FOLLOW_UP_SECONDS in
    client/.env overrides it (0 turns it off)."""
    env = os.environ.get("FOLLOW_UP_SECONDS", "").strip()
    if env:
        try:
            return max(0.0, min(15.0, float(env)))
        except ValueError:
            pass
    return DEFAULT_FOLLOW_UP_SECONDS if _read_user_settings().get("follow_up", True) else 0.0


def load_voice_commands() -> bool:
    """"Ei, Ombri" + "chega" (client/voice_commands.py). Opt-in: the mic is
    listened to locally the whole time Ombri runs. VOICE_COMMANDS in
    client/.env overrides the saved setting."""
    env = os.environ.get("VOICE_COMMANDS", "").strip().lower()
    if env:
        return env in ("1", "true", "yes", "on")
    return bool(_read_user_settings().get("voice_commands", False))


def save_user_settings(**fields) -> None:
    """Merges `fields` into %APPDATA%/Parca/settings.json (token, voice)."""
    USER_SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    data = _read_user_settings()
    for key, value in fields.items():
        data[key] = value.strip() if isinstance(value, str) else value
    USER_SETTINGS_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def save_token(token: str) -> None:
    save_user_settings(token=token)


def load_client_settings() -> ClientSettings:
    """The token comes from SERVER_AUTH_TOKEN in client/.env (development)
    or, for testers, the one saved by the first-launch window; empty if
    neither exists - client.main then opens that window."""
    return ClientSettings(
        server_url=_env("SERVER_URL", DEFAULT_SERVER_URL),
        server_auth_token=os.environ.get("SERVER_AUTH_TOKEN", "").strip() or load_saved_token(),
        voice=load_saved_voice(),
        capture_hotkey=resolve_capture_hotkey(),
        remember_hotkey=resolve_remember_hotkey(),
        gamepad_combo=resolve_gamepad_combo(),
        mic_silence_hang=_float_env("MIC_SILENCE_HANG", DEFAULT_MIC_SILENCE_HANG),
        mic_max_duration=_float_env("MIC_MAX_DURATION", DEFAULT_MIC_MAX_DURATION),
        mic_min_duration=_float_env("MIC_MIN_DURATION", DEFAULT_MIC_MIN_DURATION),
        mic_vad_threshold=_float_env("MIC_VAD_THRESHOLD", DEFAULT_MIC_VAD_THRESHOLD),
        mic_speech_pad_ms=_float_env("MIC_SPEECH_PAD_MS", DEFAULT_MIC_SPEECH_PAD_MS),
        mic_no_speech_timeout=_float_env("MIC_NO_SPEECH_TIMEOUT", DEFAULT_MIC_NO_SPEECH_TIMEOUT),
        min_speech_seconds=_float_env("MIN_SPEECH_SECONDS", DEFAULT_MIN_SPEECH_SECONDS),
        game_aliases_path=_path("GAME_ALIASES_PATH", PROJECT_ROOT / "game_aliases.json"),
        output_dir=_path("OUTPUT_DIR", PROJECT_ROOT / "output"),
        barge_in=_bool_env("BARGE_IN", DEFAULT_BARGE_IN),
        filler_delay_ms=_float_env("FILLER_DELAY_MS", DEFAULT_FILLER_DELAY_MS),
        filler_enabled=_bool_env("FILLER_ENABLED", DEFAULT_FILLER_ENABLED),
        overlay_enabled=_bool_env("OVERLAY", DEFAULT_OVERLAY_ENABLED),
        verbose_telemetry=_bool_env("VERBOSE_TELEMETRY", DEFAULT_VERBOSE_TELEMETRY),
        game_detect_poll_seconds=max(0.1, _float_env(
            "GAME_DETECT_POLL_SECONDS", DEFAULT_GAME_DETECT_POLL_SECONDS
        )),
        game_voice_match_min_ratio=_float_env(
            "GAME_VOICE_MATCH_MIN_RATIO", DEFAULT_GAME_VOICE_MATCH_MIN_RATIO
        ),
        smart_turn=_bool_env("SMART_TURN", True),
    )
