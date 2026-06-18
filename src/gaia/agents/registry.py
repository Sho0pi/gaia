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

    @property
    def directory(self) -> Path:
        """The folder holding the soul ``.md`` files."""
        return self._dir

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

    def update(self, key: str, **fields: object) -> AgentSpec | None:
        """Refine an existing soul: apply ``fields`` (e.g. description/instruction) and save.

        Returns the updated spec, or ``None`` if unknown. History/rollback is handled by the
        ``~/.gaia`` git state repo (:mod:`gaia.state`), which the caller commits to.
        """
        spec = self.get(key)
        if spec is None:
            return None
        updated = spec.model_copy(update=fields)
        self.save(updated)
        return updated

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
