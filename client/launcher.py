"""Ombri.exe: the runtime's pythonw.exe with Ombri's name and icon, so Task
Manager says "Ombri" instead of "Python" (built by tools/build_ombri_exe.py).

Ombri-Setup.exe stays the same file (SmartScreen trust is per file), so it
can't ship here: the update brings client/bin/Ombri.exe, and the first launch
copies it next to runtime\\pythonw.exe - the folder updates never touch,
where it finds python314.dll and python314._pth. It's tried once with a tiny
script before anything uses it; until that passes, everything stays on
pythonw.exe as before. Only Ombri-Setup.exe installs (runtime\\); the old
instalar-parca.cmd .venv installs keep pythonw.exe (a venv's pythonw is a
launcher for another Python, a renamed copy would not be one)."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from client.config import _read_user_settings, save_user_settings
from client.updater import INSTALL_MARKER, REPO_ROOT

LAUNCHER_NAME = "Ombri.exe"
_RELAUNCHED_ENV = "_OMBRI_EXE"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install_launcher() -> Path | None:
    """runtime\\Ombri.exe once it's in place and has run; None otherwise
    (not an Ombri-Setup.exe install, nothing shipped yet, or the try failed).
    Best-effort and silent: a failure only means Task Manager keeps saying
    Python."""
    source = REPO_ROOT / "client" / "bin" / LAUNCHER_NAME
    runtime = REPO_ROOT / "runtime"
    if os.name != "nt" or not INSTALL_MARKER.exists() or not (runtime / "pythonw.exe").exists() \
            or not source.exists():
        return None
    dest = runtime / LAUNCHER_NAME
    try:
        if not dest.exists() or _sha256(dest) != _sha256(source):
            shutil.copy2(source, dest)  # refused while it runs: the next launch copies it
        have = _sha256(dest)
    except OSError:
        return None
    if _read_user_settings().get("launcher_ok") == have:
        return dest
    try:
        ok = subprocess.run([str(dest), "-c", "import client.updater"], cwd=REPO_ROOT, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if not ok:
        return None
    save_user_settings(launcher_ok=have)
    return dest


def windowed_python() -> str:
    """The interpreter to start a windowless copy of the app with: the one
    running it unless that's a console python.exe, then its pythonw.exe."""
    exe = sys.executable
    if os.name == "nt" and Path(exe).name.lower() == "python.exe":
        windowed = Path(exe).with_name("pythonw.exe")
        if windowed.exists():
            return str(windowed)
    return exe


def relaunch_as_ombri(argv: list[str]) -> bool:
    """Started as pythonw.exe (a taskbar pin, the installer's "run now")
    with Ombri.exe ready: start the same command line as Ombri.exe and
    report True so the caller exits. Once only (the env flag), so a broken
    Ombri.exe can't loop."""
    if os.environ.get(_RELAUNCHED_ENV) or Path(sys.executable).name.lower() != "pythonw.exe":
        return False
    launcher = install_launcher()
    if launcher is None:
        return False
    try:
        subprocess.Popen([str(launcher), "-m", "client.main", *argv], cwd=REPO_ROOT,
                         env=dict(os.environ, **{_RELAUNCHED_ENV: "1"}), close_fds=True,
                         creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    except OSError:
        return False
    return True
