"""client/updater.py — startup update check against the public client repo.

Runs once, at the top of `client.main.main()`. Never blocks launch: every
failure (no network, no git, dirty tree, GitHub down) prints at most one line
and the current version keeps running.

  * An install made by Parca-Setup.exe or `instalar-parca.cmd` (marked by
    INSTALL_MARKER, no git needed): if the latest release is newer, download
    that release's zip from GitHub, sync it over the program folder (the
    Python it runs on - runtime/ from the setup, .venv from the .cmd - and
    the tester's own files - client/output, client/.env*, client/config.json
    - are never touched), reinstall requirements only if they changed, and
    tell the caller to restart.
  * A git clone of the PUBLIC repo (origin is PUBLIC_REPO): same, via
    `git pull --ff-only`.
  * A plain ZIP download: only a notice with the download URL.
  * Any other git checkout - notably the developer's private monorepo, where
    this same file lives - is skipped silently. Pulling THAT repo from a
    startup check would be wrong.

`AUTO_UPDATE=false` in client/.env turns the whole check off.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

PUBLIC_REPO = "vallanders-dev/game-companion-client"
RELEASES_URL = f"https://api.github.com/repos/{PUBLIC_REPO}/releases/latest"
DOWNLOAD_URL = f"https://github.com/{PUBLIC_REPO}/releases/latest"
PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
VERSION_FILE = PACKAGE_DIR / "VERSION"
INSTALL_MARKER = REPO_ROOT / ".parca-install"
# Never overwritten or deleted by a zip update - the Python the program runs
# on (runtime: Parca-Setup.exe's built-in one, with its uninstaller inside;
# .venv: instalar-parca.cmd's) and the tester's own state.
_PROTECTED = {"runtime", ".venv", ".parca-install", "output", "config.json", "__pycache__"}


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
    """'installed' (Parca-Setup.exe or instalar-parca.cmd), 'public' (a clone of
    PUBLIC_REPO), 'other' (any other git checkout), or 'none' (not a git
    checkout, or git isn't installed)."""
    if INSTALL_MARKER.exists():
        return "installed"
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


def _download_release_zip(version: str) -> bytes:
    url = f"https://github.com/{PUBLIC_REPO}/archive/refs/tags/v{version}.zip"
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 - fixed https URL
        return resp.read()


def _is_protected(rel: Path) -> bool:
    return any(part in _PROTECTED or part.startswith(".env") and part != ".env.example"
               for part in rel.parts)


def _apply_release_zip(data: bytes, root: Path = REPO_ROOT) -> None:
    """Makes `root` match the release: adds/overwrites every file in it and
    removes program files the release no longer has - never anything
    `_is_protected()`. GitHub's archive zips hold one top-level folder."""
    with tempfile.TemporaryDirectory() as tmp:
        zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
        tops = [d for d in Path(tmp).iterdir() if d.is_dir()]
        if len(tops) != 1:
            raise RuntimeError("zip da versão nova em formato inesperado")
        new_root = tops[0]
        new_files = {f.relative_to(new_root) for f in new_root.rglob("*") if f.is_file()}
        for rel in new_files:
            if _is_protected(rel):
                continue
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(new_root / rel, dest)
        for old in [f for f in root.rglob("*") if f.is_file()]:
            rel = old.relative_to(root)
            if rel not in new_files and not _is_protected(rel):
                old.unlink()


def _reinstall_requirements_if_changed(before: str) -> None:
    if _requirements_hash() == before:
        return
    print("  Instalando dependências novas...")
    pip = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(REPO_ROOT / "requirements.txt")],
        capture_output=True, text=True,
    )
    if pip.returncode != 0:
        print("  (falha ao instalar dependências - rode "
              "`pip install -r requirements.txt` manualmente se algo não funcionar)")


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

    if kind == "installed":
        print(f"Atualizando {current} -> {latest}...")
        before = _requirements_hash()
        try:
            _apply_release_zip(_download_release_zip(latest))
        except Exception as exc:  # noqa: BLE001 - keep running the current version
            print(f"  (não consegui atualizar: {exc} - seguindo na versão {current})")
            return False
        _reinstall_requirements_if_changed(before)
        print(f"  Atualizado para {local_version()}.")
        return True

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
    _reinstall_requirements_if_changed(before)
    print(f"  Atualizado para {local_version()}.")
    return True
