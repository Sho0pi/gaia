"""System test: God builds a real ADK root agent over learned subagents.

Skipped unless a model backend is configured, so CI stays green without secrets.
Run locally with GODPY_GOOGLE_API_KEY set to exercise the full ADK wiring.
"""

from __future__ import annotations

import os

import pytest

from godpy.agents import AgentSpec
from godpy.god import God

pytestmark = pytest.mark.skipif(
    not os.environ.get("GODPY_GOOGLE_API_KEY"),
    reason="needs a configured model backend (set GODPY_GOOGLE_API_KEY)",
)


def test_god_reuses_stored_agent_across_instances(tmp_path: object) -> None:
    os.environ["GODPY_AGENT_REGISTRY_DIR"] = str(tmp_path)
    spec = AgentSpec(
        name="Translator",
        description="Translates text between languages.",
        instruction="Translate the input as requested.",
        model="gemini-2.0-flash",
    )

    God().ensure_agent(spec)  # first run learns + stores it

    # A fresh God on the same registry must already know the agent.
    assert "translator" in God().known_agents()


def test_build_root_agent_attaches_subagents(tmp_path: object) -> None:
    os.environ["GODPY_AGENT_REGISTRY_DIR"] = str(tmp_path)
    god = God()
    god.ensure_agent(
        AgentSpec(
            name="Calc",
            description="Does arithmetic.",
            instruction="Compute the expression.",
            model="gemini-2.0-flash",
        )
    )

    root = god.build_root_agent()

    assert root.name == "god"
    assert len(root.sub_agents) == 1
