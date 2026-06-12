"""The declarative subagent description shared by the factory and registry."""

from __future__ import annotations

import re

import yaml
from pydantic import BaseModel, Field

#: Frontmatter keys, in display order. ``instruction`` (long prose) is the markdown body,
#: never the frontmatter — that is what makes a soul file pleasant to read and edit.
_FRONTMATTER_FIELDS = ("name", "description", "model", "skills", "tools", "communication_style")


def slugify(name: str) -> str:
    """Normalize a human name into a stable, identifier-safe registry key.

    ADK requires an agent's ``name`` to be a valid Python identifier (it becomes a graph
    node name), so the key uses underscores — never hyphens — and never starts with a
    digit. The same key names the agent's workspace dir, keeping the two in lockstep.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not slug:
        return "agent"
    return f"_{slug}" if slug[0].isdigit() else slug


class AgentSpec(BaseModel):
    """Declarative description of a subagent — the unit Gaia reasons about.

    Maps onto an A2A AgentCard for persistence and an ADK ``LlmAgent`` for
    execution.
    """

    name: str
    description: str
    instruction: str
    model: str
    skills: list[str] = Field(default_factory=list)
    # Tool ids to pin for this subagent; empty = every registered tool (the default).
    tools: list[str] = Field(default_factory=list)
    # Voice for this subagent; None = the factory's configured default style.
    communication_style: str | None = None

    @property
    def key(self) -> str:
        return slugify(self.name)

    def to_markdown(self) -> str:
        """Serialize as a human-friendly Markdown file: YAML frontmatter + instruction body."""
        meta = {field: getattr(self, field) for field in _FRONTMATTER_FIELDS}
        front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
        return f"---\n{front}\n---\n\n{self.instruction.strip()}\n"

    @classmethod
    def from_markdown(cls, text: str) -> AgentSpec:
        """Parse the :meth:`to_markdown` format back into a spec (raises on malformed input)."""
        if not text.lstrip().startswith("---"):
            raise ValueError("missing YAML frontmatter (expected a leading '---' line)")
        _, front, body = text.lstrip().split("---", 2)
        meta = yaml.safe_load(front) or {}
        if not isinstance(meta, dict):
            raise ValueError("frontmatter must be a mapping of fields")
        return cls.model_validate({**meta, "instruction": body.strip()})
