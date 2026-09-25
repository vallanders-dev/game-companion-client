"""client/updater.py — startup update check against the public client repo.

Runs once, at the top of `client.main.main()`. Never blocks launch: every
failure (no network, no git, dirty tree, GitHub down) prints at most one line
and the current version keeps running.

  * A git clone of the PUBLIC repo (origin is PUBLIC_REPO): if the latest
    GitHub release is newer than `client/VERSION`, `git pull --ff-only`,
    reinstall requirements only if `requirements.txt` changed, and tell the
    caller to restart so the new code runs this session.
  * A ZIP download (not a git checkout): only a notice with the download URL.
  * Any other git checkout - notably the developer's private monorepo, where
    this same file lives - is skipped silently. Pulling THAT repo from a
    startup check would be wrong.

`AUTO_UPDATE=false` in client/.env turns the whole check off.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

PUBLIC_REPO = "vallanders-dev/game-companion-client"
RELEASES_URL = f"https://api.github.com/repos/{PUBLIC_REPO}/releases/latest"
DOWNLOAD_URL = f"https://github.com/{PUBLIC_REPO}/releases/latest"
PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
VERSION_FILE = PACKAGE_DIR / "VERSION"


def local_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


def _parse(version: str) -> tuple[int, ...]:
    parts = []
    for piece in version.strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _latest_release_version(timeout: float = 5.0) -> str | None:
    req = urllib.request.Request(RELEASES_URL, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
        tag = json.load(resp).get("tag_name") or ""
    return tag.lstrip("vV") or None


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, timeout=60,
    )


def _checkout_kind() -> str:
    """'public' (a clone of PUBLIC_REPO), 'other' (any other git checkout),
    or 'none' (not a git checkout, or git isn't installed)."""
    try:
        result = _git("remote", "get-url", "origin")
    except (OSError, subprocess.SubprocessError):
        return "none"
    if result.returncode != 0:
        return "none" if not (REPO_ROOT / ".git").exists() else "other"
    url = result.stdout.strip().lower().replace("\\", "/").removesuffix(".git")
    return "public" if url.endswith(PUBLIC_REPO.lower()) else "other"


def _requirements_hash() -> str:
    try:
        return hashlib.sha256((REPO_ROOT / "requirements.txt").read_bytes()).hexdigest()
    except OSError:
        return ""


def check_for_update() -> bool:
    """Returns True if the code on disk was just updated and the caller
    should restart itself; False in every other case."""
    if os.environ.get("AUTO_UPDATE", "").strip().lower() in ("0", "false", "no", "off"):
        return False
    kind = _checkout_kind()
    if kind == "other":
        return False
    current = local_version()
    try:
        latest = _latest_release_version()
    except Exception:  # noqa: BLE001 - offline / GitHub unreachable: run what we have
        return False
    if not latest or _parse(latest) <= _parse(current):
        return False

    if kind == "none":
        print(f"Nova versão disponível ({current} -> {latest}): baixe em {DOWNLOAD_URL}")
        return False

    if _git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        print(f"Nova versão disponível ({current} -> {latest}), mas há arquivos alterados na pasta "
              "do programa - atualização automática pulada. Rode `git pull` manualmente.")
        return False

    print(f"Atualizando {current} -> {latest}...")
    before = _requirements_hash()
    pulled = _git("pull", "--ff-only")
    if pulled.returncode != 0:
        print(f"  (não consegui atualizar: {pulled.stderr.strip()[:200]} - seguindo na versão {current})")
        return False
    if _requirements_hash() != before:
        print("  Instalando dependências novas...")
        pip = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(REPO_ROOT / "requirements.txt")],
            capture_output=True, text=True,
        )
        if pip.returncode != 0:
            print("  (falha ao instalar dependências - rode "
                  "`pip install -r requirements.txt` manualmente se algo não funcionar)")
    print(f"  Atualizado para {local_version()}.")
    return True
