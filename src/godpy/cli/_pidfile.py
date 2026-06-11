"""Daemon pidfile helpers (``~/.godpy/godpy.pid``).

The serve process writes its own pidfile once startup is committed and removes it on
exit; ``godpy stop`` removes it after a SIGKILL fallback (the killed child can't).
All functions read the module-global ``PID_FILE`` at call time so tests can
monkeypatch it. v1 accepts the tiny pid-reuse race between ``kill(pid, 0)`` and a
later signal — stale files are removed eagerly instead of pulling in psutil.
"""

from __future__ import annotations

import os

from godpy import constants

#: Module-level alias (not a bare re-export): tests monkeypatch this to a tmp path,
#: and every function below reads it at call time.
PID_FILE = constants.PID_FILE


def read() -> int | None:
    """The pid stored in the file, or ``None`` (missing or garbage content)."""
    try:
        return int(PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def alive(pid: int) -> bool:
    """True if a process with ``pid`` exists (signal 0 probe)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists but owned by someone else — treat as alive
        return True
    return True


def read_live() -> int | None:
    """The pid of the live daemon, or ``None``. Stale/garbage files are removed eagerly."""
    pid = read()
    if pid is not None and alive(pid):
        return pid
    remove()  # dead pid or garbage content — clean up
    return None


def write(pid: int | None = None) -> None:
    """Record ``pid`` (default: this process) as the running daemon."""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(f"{pid if pid is not None else os.getpid()}\n")


def remove() -> None:
    """Delete the pidfile. Idempotent: serve's finally and ``stop`` may both call it."""
    PID_FILE.unlink(missing_ok=True)
