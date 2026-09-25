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

DEFAULT_SERVER_URL = "ws://127.0.0.1:8000/v1/ws"
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
DEFAULT_OVERLAY_ENABLED = False  # off by default for this first real client cut - see client/main.py's docstring
DEFAULT_TRAY_ENABLED = False     # ditto
DEFAULT_VERBOSE_TELEMETRY = True
DEFAULT_GAME_DETECT_POLL_SECONDS = 1.0
DEFAULT_GAME_VOICE_MATCH_MIN_RATIO = 0.72


class ConfigError(RuntimeError):
    """Raised when a required environment variable is missing."""


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
    tray_enabled: bool
    verbose_telemetry: bool
    game_detect_poll_seconds: float
    game_voice_match_min_ratio: float


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


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Environment variable {name} is not set (client/.env).")
    return value


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


def load_client_settings(require_server: bool = True) -> ClientSettings:
    """``require_server=False`` skips the ``SERVER_AUTH_TOKEN`` check -
    not currently used by anything (there's no client-side equivalent of
    ``ingest`` that needs to run without one), kept for symmetry with
    ``server/config.py``'s identical escape hatch."""

    def secret(name: str) -> str:
        return _require(name) if require_server else os.environ.get(name, "").strip()

    return ClientSettings(
        server_url=_env("SERVER_URL", DEFAULT_SERVER_URL),
        server_auth_token=secret("SERVER_AUTH_TOKEN"),
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
        tray_enabled=_bool_env("TRAY", DEFAULT_TRAY_ENABLED),
        verbose_telemetry=_bool_env("VERBOSE_TELEMETRY", DEFAULT_VERBOSE_TELEMETRY),
        game_detect_poll_seconds=max(0.1, _float_env(
            "GAME_DETECT_POLL_SECONDS", DEFAULT_GAME_DETECT_POLL_SECONDS
        )),
        game_voice_match_min_ratio=_float_env(
            "GAME_VOICE_MATCH_MIN_RATIO", DEFAULT_GAME_VOICE_MATCH_MIN_RATIO
        ),
    )
