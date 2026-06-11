"""The declarative subagent description shared by the factory and registry."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field


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
