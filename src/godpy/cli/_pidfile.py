"""Daemon pidfile (``~/.godpy/godpy.pid``) as a small value object.

The serve process writes its own pidfile once startup is committed and removes it on
exit; ``godpy stop`` removes it after a SIGKILL fallback (the killed child can't).
``PidFile()`` defaults to the real location; tests construct ``PidFile(tmp_path)``
directly — no module-level state to patch. v1 accepts the tiny pid-reuse race between
``kill(pid, 0)`` and a later signal — stale files are removed eagerly instead of
pulling in psutil.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from godpy import constants


@dataclass(frozen=True, slots=True)
class PidFile:
    """The daemon's pidfile: read/write/liveness around one well-known path."""

    path: Path = field(default_factory=lambda: constants.PID_FILE)

    def read(self) -> int | None:
        """The pid stored in the file, or ``None`` (missing or garbage content)."""
        try:
            return int(self.path.read_text().strip())
        except (FileNotFoundError, ValueError):
            return None

    @staticmethod
    def alive(pid: int) -> bool:
        """True if a process with ``pid`` exists (signal 0 probe)."""
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:  # exists but owned by someone else — treat as alive
            return True
        return True

    def read_live(self) -> int | None:
        """The pid of the live daemon, or ``None``. Stale/garbage files are removed eagerly."""
        pid = self.read()
        if pid is not None and self.alive(pid):
            return pid
        self.remove()  # dead pid or garbage content — clean up
        return None

    def write(self, pid: int | None = None) -> None:
        """Record ``pid`` (default: this process) as the running daemon."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(f"{pid if pid is not None else os.getpid()}\n")

    def remove(self) -> None:
        """Delete the pidfile. Idempotent: serve's finally and ``stop`` may both call it."""
        self.path.unlink(missing_ok=True)
