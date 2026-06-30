"""Soul projects: the ``(user, soul)`` current-project pointer + per-project ``PROJECT.md``.

A soul scopes its work to a *project* dir (``workspace/<project>``). The model picks the project
name when it delegates, but it isn't reliable — it omits it, passes a sentence, or **invents a new
slug for the same app** (``hsk1-flashcards`` vs ``chinese-flashcard-style``), forking the workspace.

Two pieces fight that:
* :class:`ProjectStore` — a persistent ``"<user>:<soul>" -> current slug`` map
  (``~/.gaia/projects.json``)
  so an omitted delegation *continues* the same app across ``/reset``/restart (the warm-session map
  is in-memory and dies).
* Each project's **``PROJECT.md``** (YAML frontmatter ``name``/``description`` + a markdown body of
  rules/notes, exactly like ``SKILL.md``) lets routing match on *meaning* — only the frontmatter is
  read for listing/matching (cheap, progressive disclosure); the soul reads the body on demand.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import yaml

from gaia import constants

#: The per-project metadata file (frontmatter + rules body), the SKILL.md convention.
PROJECT_MD = "PROJECT.md"


def read_project_description(project_dir: Path) -> str:
    """A project's ``PROJECT.md`` frontmatter ``description`` (frontmatter only), or ``""``.

    Never reads the body (rules); listing/matching needs only the one-liner, like ``list_skills``
    shows a skill's frontmatter not its instructions.
    """
    md = Path(project_dir) / PROJECT_MD
    if not md.is_file():
        return ""
    return str(_frontmatter(md.read_text()).get("description", "")).strip()


def write_project_md(project_dir: Path, name: str, description: str) -> None:
    """Create ``<project_dir>/PROJECT.md`` (frontmatter + a starter rules body) if absent.

    Never clobbers an existing one — the soul keeps its rules/notes in the body.
    """
    md = Path(project_dir) / PROJECT_MD
    if md.is_file():
        return
    project_dir.mkdir(parents=True, exist_ok=True)
    front = yaml.safe_dump(
        {"name": name, "description": description}, sort_keys=False, allow_unicode=True
    ).strip()
    body = (
        f"# {name}\n\n{description}\n\n## Rules & notes\n"
        "- Keep this project's conventions, decisions, and gotchas here so edits stay consistent.\n"
    )
    md.write_text(f"---\n{front}\n---\n\n{body}")


def _frontmatter(text: str) -> dict[str, object]:
    """Parse the leading ``---…---`` YAML block (``skills.py`` idiom); ``{}`` if none/malformed."""
    if not text.startswith("---"):
        return {}
    try:
        _, front, _ = text.split("---", 2)
        data = yaml.safe_load(front)
    except (ValueError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


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
