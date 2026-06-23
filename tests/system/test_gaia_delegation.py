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


async def test_delegated_soul_asks_the_user_then_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # P2 end-to-end against a real model: a soul that calls ask_user pauses the run
    # (execute_decision returns SoulRun.pending), and feeding the answer resumes the SAME run so
    # it finishes its work. Drives the soul layer directly to avoid depending on the root's
    # delegation decision and the smith.
    from gaia import constants
    from gaia.souls.run import execute_decision, resume_soul
    from gaia.souls.smith import SoulDecision

    monkeypatch.setattr(constants, "AGENTS_DIR", tmp_path / "agents")
    config = tmp_path / "gaia.yaml"
    config.write_text("memory:\n  enabled: false\n")
    gaia = Gaia(Settings(agent_registry_dir=tmp_path / "reg", config_path=config))
    spec = AgentSpec(
        name="Key Asker",
        description="Sets up an integration that needs a credential.",
        instruction=(
            "You are setting up an integration that needs an API key you do not have. FIRST, "
            "call ask_user(question='What is your API key?'). Once you receive the key, write a "
            "file named key.txt whose only contents are exactly the key, then reply 'SAVED'."
        ),
        model=gaia.settings.model,
    )

    run = await execute_decision(
        gaia,
        SoulDecision(action="forge", reason="needs a key", spec=spec),
        "set up the integration",
        user_id="tester",
    )
    assert run.pending is not None, f"soul did not pause on ask_user (ok={run.ok}, err={run.error})"
    assert run.pending.soul_fc_id and "key" in run.pending.question.lower()

    final = await resume_soul(gaia, run.pending, "sk-LIVE-42")
    assert final.ok, f"resume failed: {final.error}"
    assert (Path(final.workspace) / "key.txt").read_text().strip() == "sk-LIVE-42"
