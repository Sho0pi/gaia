"""The soul-smith: Gaia's agent-designer.

Given a task and the souls Gaia already knows, the smith decides whether one of them fits
(reuse) or a brand-new specialist should be forged — and if so, authors its
:class:`~gaia.agents.spec.AgentSpec`. It is a pure decision agent (no tools); ADK's
``output_schema`` makes it return a validated :class:`SoulDecision`. The ADK import is
deferred so this module stays importable without a model backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, model_validator

from gaia.agents.spec import AgentSpec

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.agents import LlmAgent

#: The smith agent's name (also its nested-runner agent name).
NAME = "soul_smith"


class SoulDecision(BaseModel):
    """The soul-smith's verdict: reuse an existing soul or forge a new one."""

    action: Literal["reuse", "forge"] = Field(description="Whether to reuse or forge a soul.")
    reason: str = Field(default="", description="One-line justification, surfaced to the user.")
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
You are the soul-smith, Gaia's agent-designer. A "soul" is a **reusable role specialist** —
think of building a small company of generalist professionals you hire again and again, NOT a
new hire per task. Think "frontend-designer", "personal-trainer", "copywriter" — a ROLE that
serves many future tasks — never a one-off like "gym-site-designer" or "ab-plan-writer".

You are given a TASK and a list of EXISTING SOULS (each as "key: description"). Decide:

- **Strongly prefer reuse.** If any existing soul's ROLE could plausibly do this task, return
  action="reuse" with soul_key set to that soul's exact key (MUST be one of the listed keys —
  never invent one). Judge by the role, not an exact task match: a "frontend-designer" handles
  any website, a "personal-trainer" handles any workout program. Forge only when the roster
  genuinely lacks a fitting role.
- Otherwise return action="forge" and set spec to a new **generic role** soul:
    * name: the profession/role, reusable across missions (e.g. "Frontend Designer",
      "Personal Trainer", "Copywriter") — NOT the specific task.
    * description: ONE sentence describing the ROLE's domain (what this professional does in
      general), so Gaia can route future tasks to it. Generic, not task-specific.
    * instruction: the soul's system prompt for that role. Tell it to actually DO the task it
      is given and to WRITE every deliverable as files in its workspace via the fs_write tool
      (e.g. index.html, program.md) — it must produce files, not just describe them. It may
      consult_soul(question) for a quick expert answer, or task_create(...) to split off a
      sub-deliverable and yield.
    * model: "{model}"
    * skills: []   tools: []   (it automatically gets every tool)

Always set reason to a short justification. Return only the structured decision.
"""


def build_soul_smith(model: str, provider: str = "gemini", use_oauth: bool = False) -> LlmAgent:
    """Build the soul-smith ADK agent, bound to ``model``/``provider`` for the forged specs."""
    from google.adk.agents import LlmAgent

    from gaia.models import resolve_model

    return LlmAgent(
        name=NAME,
        model=resolve_model(model, provider=provider, use_oauth=use_oauth),
        description="Designs or selects the right specialist soul for a task.",
        instruction=_INSTRUCTION.format(model=model),
        output_schema=SoulDecision,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )
