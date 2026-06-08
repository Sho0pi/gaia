"""The declarative subagent description shared by the factory and registry."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field


def slugify(name: str) -> str:
    """Normalize a human name into a stable registry key."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


class AgentSpec(BaseModel):
    """Declarative description of a subagent — the unit God reasons about.

    Maps onto an A2A AgentCard for persistence and an ADK ``LlmAgent`` for
    execution.
    """

    name: str
    description: str
    instruction: str
    model: str
    skills: list[str] = Field(default_factory=list)
    # Tool ids resolved against the factory's ToolRegistry into callables ADK invokes.
    tools: list[str] = Field(default_factory=list)
    # Voice for this subagent; None = the factory's configured default style.
    communication_style: str | None = None

    @property
    def key(self) -> str:
        return slugify(self.name)
