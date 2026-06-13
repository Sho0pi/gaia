"""System test: Gaia builds a real ADK root agent over learned subagents.

Skipped unless a Gemini key is configured (via .env GEMINI_API_KEY), so CI stays
green without secrets. Locally, the .env is loaded in conftest and these run.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gaia.agents import AgentSpec
from gaia.config import Settings
from gaia.core import Gaia

pytestmark = [
    pytest.mark.system,
    pytest.mark.skipif(
        not os.environ.get("GEMINI_API_KEY"),
        reason="needs a Gemini key (set GEMINI_API_KEY in .env)",
    ),
]


def _spec(name: str, model: str) -> AgentSpec:
    return AgentSpec(
        name=name,
        description=f"{name} specialist.",
        instruction=f"Act as the {name} specialist.",
        model=model,
    )


def test_gaia_reuses_stored_agent_across_instances(tmp_path: Path) -> None:
    settings = Settings(agent_registry_dir=tmp_path)

    Gaia(settings).ensure_agent(_spec("Translator", settings.model))

    # A fresh Gaia on the same registry must already know the agent (no recreate).
    assert "translator" in Gaia(settings).known_souls()


def test_build_root_agent_attaches_subagents(tmp_path: Path) -> None:
    settings = Settings(agent_registry_dir=tmp_path)
    gaia = Gaia(settings)
    gaia.ensure_agent(_spec("Calc", settings.model))

    root = gaia.build_root_agent()

    assert root.name == "gaia"
    assert len(root.sub_agents) == 1
