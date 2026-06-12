"""Persist souls (subagent specs) as Markdown on disk so they are reused, never recreated.

Each soul is one ``<key>.md`` file: YAML frontmatter (name/description/model/skills/
tools/style) over the instruction body, so a soul reads and edits like a normal note
(see :meth:`AgentSpec.to_markdown`). Pure stdlib + pydantic + yaml — no model backend
needed, fully unit-testable.
"""

from __future__ import annotations

from pathlib import Path

from gaia.agents.spec import AgentSpec


class SoulRegistry:
    """File-backed store of souls (:class:`AgentSpec`), one JSON file per soul key."""

    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.md"

    def get(self, key: str) -> AgentSpec | None:
        """Return the stored soul for ``key``, or ``None`` if not yet learned."""
        path = self._path(key)
        if not path.exists():
            return None
        return AgentSpec.from_markdown(path.read_text())

    def save(self, spec: AgentSpec) -> None:
        """Persist ``spec`` so the next matching task reuses it."""
        self._path(spec.key).write_text(spec.to_markdown())

    def list_keys(self) -> list[str]:
        """All learned soul keys, sorted."""
        return sorted(p.stem for p in self._dir.glob("*.md"))

    def delete(self, key: str) -> bool:
        """Remove the soul's file. True if it existed, False if there was nothing to delete."""
        path = self._path(key)
        if not path.exists():
            return False
        path.unlink()
        return True
