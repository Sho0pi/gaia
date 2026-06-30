"""The soul project store: ``~/.gaia/projects.json``, each ``(user, soul)``'s current project.

A soul scopes its work to a *project* dir (``workspace/<project>``). The model picks the project
name when it delegates, but it isn't reliable — it sometimes omits it or passes a sentence, and
the in-memory warm-session map dies on ``/reset``/restart — so the same app kept forking into new
workspaces. This persists the last project each ``(user, soul)`` used, so a delegation that omits
the project *continues* the same app instead of starting a fresh one.

A small JSON map (``"<user_id>:<soul_key>" -> slug``), atomically rewritten — the users-store way.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from gaia import constants


class ProjectStore:
    """File-backed ``(user, soul) -> current project slug`` map; atomically rewritten on change."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else constants.PROJECTS_FILE
        self._lock = threading.RLock()  # one shared singleton, written from connector threads too

    def get(self, user_id: str, soul_key: str) -> str:
        """The last project ``(user_id, soul_key)`` worked on, or ``""`` if none yet."""
        return self._load().get(self._key(user_id, soul_key), "")

    def set(self, user_id: str, soul_key: str, project: str) -> None:
        """Record ``project`` as the current one for ``(user_id, soul_key)``."""
        with self._lock:
            data = self._load()
            data[self._key(user_id, soul_key)] = project
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
            os.replace(tmp, self._path)  # atomic on POSIX

    @staticmethod
    def _key(user_id: str, soul_key: str) -> str:
        return f"{user_id}:{soul_key}"

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text() or "{}")
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
