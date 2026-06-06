"""System test: God builds a real ADK root agent over learned subagents.

Skipped unless a Gemini key is configured (via .env GEMINI_API_KEY), so CI stays
green without secrets. Locally, the .env is loaded in conftest and these run.
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


def _spec(name: str, model: str) -> AgentSpec:
    return AgentSpec(
        name=name,
        description=f"{name} specialist.",
        instruction=f"Act as the {name} specialist.",
        model=model,
    )


def test_god_reuses_stored_agent_across_instances(tmp_path: Path) -> None:
    settings = Settings(agent_registry_dir=tmp_path)

    God(settings).ensure_agent(_spec("Translator", settings.model))

    # A fresh God on the same registry must already know the agent (no recreate).
    assert "translator" in God(settings).known_agents()


def test_build_root_agent_attaches_subagents(tmp_path: Path) -> None:
    settings = Settings(agent_registry_dir=tmp_path)
    god = God(settings)
    god.ensure_agent(_spec("Calc", settings.model))

    root = god.build_root_agent()

    assert root.name == "god"
    assert len(root.sub_agents) == 1
