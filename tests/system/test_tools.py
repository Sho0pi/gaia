"""System test: a subagent that declares a tool builds with that tool attached.

Skipped unless a Gemini key is configured, so CI stays green without secrets.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from godpy.agents import AgentSpec
from godpy.config import Settings
from godpy.god import God

pytestmark = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="needs a Gemini key (set GEMINI_API_KEY in .env)",
)


def test_subagent_with_web_search_builds(tmp_path: Path) -> None:
    settings = Settings(agent_registry_dir=tmp_path)
    god = God(settings)
    spec = AgentSpec(
        name="Researcher",
        description="Looks things up online.",
        instruction="Research the user's question using web_search.",
        model=settings.model,
        tools=["web_search"],
    )

    agent = god.ensure_agent(spec)

    assert agent.name == "researcher"
    assert len(agent.tools) == 1
