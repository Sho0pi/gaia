"""Persist souls (subagent specs) as JSON on disk so they are reused, never recreated.

Pure stdlib + pydantic — no model backend needed, fully unit-testable.
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
        return self._dir / f"{key}.json"

    def get(self, key: str) -> AgentSpec | None:
        """Return the stored soul for ``key``, or ``None`` if not yet learned."""
        path = self._path(key)
        if not path.exists():
            return None
        return AgentSpec.model_validate_json(path.read_text())

    def save(self, spec: AgentSpec) -> None:
        """Persist ``spec`` so the next matching task reuses it."""
        self._path(spec.key).write_text(spec.model_dump_json(indent=2))

    def list_keys(self) -> list[str]:
        """All learned soul keys, sorted."""
        return sorted(p.stem for p in self._dir.glob("*.json"))
