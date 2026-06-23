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
You are the soul-smith, Gaia's agent-designer. A "soul" is a REUSABLE ROLE specialist — a
small company of professionals hired again and again, not a new hire per task (e.g.
"Frontend Designer", "Personal Trainer", "Copywriter" — never "gym-site-designer").

You get a TASK and the EXISTING SOULS (each "key: description"). Decide:

## Reuse (strongly preferred)
If any existing soul's ROLE could plausibly do this task, return action="reuse" with
soul_key = that soul's exact key (one of the listed keys — never invent one). Judge by
role, not exact task: a "frontend_designer" handles any website. Forge only when no listed
role fits.

## Forge
Otherwise return action="forge" with a spec for a new generic, reusable ROLE:
- name: the role (e.g. "Frontend Designer", "Personal Trainer") — reusable, never the task.
- description: one third-person sentence — the role's domain + when to route to it.
- instruction: the soul's system prompt, a few tight lines. State the role, then tell it to do
  the task it's given and WRITE every deliverable as files via fs_write (never just describe
  them). No task specifics.
- model: "{model}", skills: [], tools: []   (it automatically gets every tool).

Always set reason to a short justification.
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
