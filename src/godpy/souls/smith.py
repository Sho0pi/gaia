"""The soul-smith: God's agent-designer.

Given a task and the souls God already knows, the smith decides whether one of them fits
(reuse) or a brand-new specialist should be forged — and if so, authors its
:class:`~godpy.agents.spec.AgentSpec`. It is a pure decision agent (no tools); ADK's
``output_schema`` makes it return a validated :class:`SoulDecision`. The ADK import is
deferred so this module stays importable without a model backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator

from godpy.agents.spec import AgentSpec

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.agents import LlmAgent

#: The smith agent's name (also its nested-runner agent name).
NAME = "soul_smith"


class SoulDecision(BaseModel):
    """The soul-smith's verdict: reuse an existing soul or forge a new one."""

    action: Literal["reuse", "forge"] = Field(description="Whether to reuse or forge a soul.")
    reason: str = Field(description="One-line justification, surfaced to the user.")
    soul_key: str | None = Field(
        default=None, description="When action='reuse': the key of an existing soul to reuse."
    )
    spec: AgentSpec | None = Field(
        default=None, description="When action='forge': the new soul to create."
    )

    @model_validator(mode="after")
    def _consistent(self) -> SoulDecision:
        """A 'reuse' must name a soul_key; a 'forge' must carry a spec."""
        if self.action == "reuse" and not self.soul_key:
            raise ValueError("action 'reuse' requires soul_key")
        if self.action == "forge" and self.spec is None:
            raise ValueError("action 'forge' requires spec")
        return self


_INSTRUCTION = """\
You are the soul-smith, God's agent-designer. A "soul" is a reusable specialist subagent.

You are given a TASK and a list of EXISTING SOULS (each as "key: description"). Decide:

- If an existing soul clearly fits the task, return action="reuse" and set soul_key to that
  soul's exact key (it MUST be one of the listed keys — never invent one).
- Otherwise return action="forge" and set spec to a new soul:
    * name: a short, specific, human title for the specialist (e.g. "Web Designer").
    * description: ONE specific sentence describing what this soul is for — this is what God
      reads later to route future tasks, so make it precise, not generic.
    * instruction: the soul's system prompt. Tell it to actually DO the task and to WRITE every
      deliverable as files in its workspace using the fs_write tool (e.g. index.html, style.css)
      — it must produce files, not just describe them.
    * model: "{model}"
    * skills: []   tools: []   (it automatically gets every tool)

Always set reason to a short justification. Return only the structured decision.
"""


def build_soul_smith(model: str, provider: str = "gemini") -> LlmAgent:
    """Build the soul-smith ADK agent, bound to ``model``/``provider`` for the forged specs."""
    from google.adk.agents import LlmAgent

    from godpy.models import resolve_model

    return LlmAgent(
        name=NAME,
        model=resolve_model(model, provider=provider),
        description="Designs or selects the right specialist soul for a task.",
        instruction=_INSTRUCTION.format(model=model),
        output_schema=SoulDecision,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )
