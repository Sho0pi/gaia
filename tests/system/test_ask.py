"""System test: a subagent that declares the ask tool builds with it attached.

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


def test_subagent_with_ask_builds(tmp_path: Path) -> None:
    # ask needs no config; it is on by default.
    settings = Settings(agent_registry_dir=tmp_path, config_path=tmp_path / "god.yaml")
    god = God(settings)
    spec = AgentSpec(
        name="Clarifier",
        description="Asks the user when unsure.",
        instruction="When a request is ambiguous, use ask to clarify.",
        model=settings.model,
        tools=["ask"],
    )

    agent = god.ensure_agent(spec)

    assert agent.name == "clarifier"
    assert len(agent.tools) == 1
    assert getattr(agent.tools[0], "is_long_running", False) is True
