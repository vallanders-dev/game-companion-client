"""client/updater.py — startup update check.

Runs once, at the top of `client.main.main()`. Never blocks launch: every
failure (no network, no git, dirty tree, server down) prints at most one line
and the current version keeps running.

Where the newest version comes from (since v0.1.12, 2026-10-02): Parça's own
server, `UPDATE_BASE/v1/client/latest`, which also serves the release zip
with its sha256 - checked before anything is written, so a damaged or
swapped download is refused. Only if our server can't answer does it fall
back to the public GitHub repo (the way v0.1.0-v0.1.11 updated). Moving off
GitHub lets the client repo go private and the files move anywhere later.

  * An install made by Parca-Setup.exe or `instalar-parca.cmd` (marked by
    INSTALL_MARKER, no git needed): if the latest release is newer, download
    that release's zip, sync it over the program folder (the
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
# Our server; PARCA_UPDATE_BASE overrides it (tests, a local server).
UPDATE_BASE = os.environ.get("PARCA_UPDATE_BASE", "").strip().rstrip("/") or "https://api.parcaplay.com"
DOWNLOAD_URL = "https://parcaplay.com/baixar"
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


def _latest_release(timeout: float = 5.0) -> dict | None:
    """{"version", "zip_url", "sha256"}: from our server, or - if it can't
    answer - from GitHub (no sha256 there). None when neither knows."""
    try:
        with urllib.request.urlopen(f"{UPDATE_BASE}/v1/client/latest", timeout=timeout) as resp:  # noqa: S310
            info = json.load(resp)
        version = str(info.get("version") or "").lstrip("vV")
        if version and info.get("zip") and info.get("zip_sha256"):
            return {"version": version, "zip_url": f"{UPDATE_BASE}/releases/{info['zip']}",
                    "sha256": str(info["zip_sha256"]).lower()}
    except Exception:  # noqa: BLE001 - try GitHub instead
        pass
    req = urllib.request.Request(RELEASES_URL, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
        tag = (json.load(resp).get("tag_name") or "").lstrip("vV")
    if not tag:
        return None
    return {"version": tag, "zip_url": f"https://github.com/{PUBLIC_REPO}/archive/refs/tags/v{tag}.zip",
            "sha256": None}


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


def _download_release_zip(release: dict) -> bytes:
    with urllib.request.urlopen(release["zip_url"], timeout=120) as resp:  # noqa: S310 - our server or GitHub
        data = resp.read()
    if release.get("sha256") and hashlib.sha256(data).hexdigest() != release["sha256"]:
        raise RuntimeError("o arquivo baixado não confere (sha256) - atualização recusada")
    return data


def _is_protected(rel: Path) -> bool:
    return any(part in _PROTECTED or part.startswith(".env") and part != ".env.example"
               for part in rel.parts)


def _safe_install_root(root: Path) -> None:
    """Refuses any folder that isn't a real Parça install. This sync deletes
    files the release doesn't have, so pointed at the wrong folder it wipes
    it - on 2026-10-02 a test harness that swapped REPO_ROOT (which the old
    `root=REPO_ROOT` default had already captured at import) ran it against
    the developer's own repository and deleted most of it, git data
    included. An install always has the marker and never a .git folder."""
    if not (root / ".parca-install").is_file() or (root / ".git").exists():
        raise RuntimeError(f"{root} não é uma instalação do Parça - atualização recusada")


def _apply_release_zip(data: bytes, root: Path | None = None) -> None:
    """Makes `root` (default: REPO_ROOT, read at call time) match the
    release: adds/overwrites every file in it and removes program files the
    release no longer has - never anything `_is_protected()`. The zip holds
    one top-level folder. Only ever on a real install (`_safe_install_root`)."""
    root = Path(root) if root is not None else REPO_ROOT
    _safe_install_root(root)
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
        release = _latest_release()
    except Exception:  # noqa: BLE001 - offline / nobody reachable: run what we have
        return False
    latest = release["version"] if release else ""
    if not latest or _parse(latest) <= _parse(current):
        return False

    if kind == "none":
        print(f"Nova versão disponível ({current} -> {latest}): baixe em {DOWNLOAD_URL}")
        return False

    if kind == "installed":
        print(f"Atualizando {current} -> {latest}...")
        before = _requirements_hash()
        try:
            _apply_release_zip(_download_release_zip(release))
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
