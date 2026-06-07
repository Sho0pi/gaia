"""System test: communication style resolves onto the built agent (no model call).

Builds real ADK ``LlmAgent``s (construction is offline) and asserts the configured
voice lands in the instruction. God -> caveman; an unstyled subagent -> default human.
"""

from __future__ import annotations

from pathlib import Path

from godpy.agents import AgentFactory, AgentRegistry, AgentSpec
from godpy.communication import CAVEMAN_PROMPT, HUMAN_PROMPT


def test_default_human_and_override_caveman(tmp_path: Path) -> None:
    registry = AgentRegistry(tmp_path / "registry")
    factory = AgentFactory(
        registry, default_model="gemini-2.0-flash", default_communication_style="human"
    )

    plain = AgentSpec(name="Plain", description="d", instruction="Do work.", model="")
    caveman = plain.model_copy(update={"name": "Cave", "communication_style": "caveman"})

    plain_agent = factory.create_or_reuse(plain)
    cave_agent = factory.create_or_reuse(caveman)

    assert plain_agent.instruction.startswith(HUMAN_PROMPT)
    assert cave_agent.instruction.startswith(CAVEMAN_PROMPT)
