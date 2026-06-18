"""The analyst: gaia's growth agent — turns a usage digest into proposals.

Smith-style pure decision agent (see :mod:`gaia.souls.smith`): no tools, ADK
``output_schema`` returns a validated :class:`AnalysisReport`. Every proposal is only a
*proposal* — ``gaia analyze`` shows each one to the human and writes nothing without
approval. The ADK import is deferred so this module imports without a model backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.agents import LlmAgent

#: The analyst agent's name (also its nested-runner agent name).
NAME = "event_analyst"


class SkillProposal(BaseModel):
    """A recurring behaviour worth distilling into a reusable skill folder."""

    name: str = Field(description="Short kebab-case skill id (becomes the folder name).")
    description: str = Field(description="One line: what the skill does / when it applies.")
    instructions: str = Field(description="The SKILL.md body: how the agent should behave.")
    rationale: str = Field(description="The digest evidence behind this proposal.")


class MemoryProposal(BaseModel):
    """A durable user fact worth writing to long-term memory."""

    user_id: str = Field(description="The user this fact belongs to (from the digest).")
    fact: str = Field(description="A short, self-contained statement about the user.")
    rationale: str = Field(description="The digest evidence behind this proposal.")


class SoulProposal(BaseModel):
    """A new specialist soul to forge, or an existing one to refine."""

    action: str = Field(description="'create' a new soul, or 'refine' an existing one.")
    key: str = Field(description="For 'refine': the existing soul key. For 'create': leave blank.")
    name: str = Field(description="Human name for the soul (its key is slugged from this).")
    description: str = Field(description="One-line ROLE (a reusable specialist, not a task).")
    instruction: str = Field(description="The soul's system prompt / how it should work.")
    rationale: str = Field(description="The digest evidence behind this proposal.")


class AnalysisReport(BaseModel):
    """The analyst's verdict over one digest window."""

    summary: str = Field(description="2-3 sentences on what the usage shows.")
    skills: list[SkillProposal] = Field(default_factory=list)
    memories: list[MemoryProposal] = Field(default_factory=list)
    souls: list[SoulProposal] = Field(default_factory=list)


_INSTRUCTION = """\
You are gaia's growth analyst. You receive a DIGEST: an aggregated summary of how the
user actually used the agent over a time window (turn counts, tool-call frequencies,
recurring tool sequences, slash commands, errors). You never see raw messages.

Propose durable artifacts ONLY where the evidence is strong:

- A SKILL when a behaviour clearly recurs and could be done better with standing
  instructions (e.g. the same tool sequence appearing many times, repeated command
  patterns, frequent errors a procedure would avoid). A skill has a kebab-case name,
  a one-line description, and markdown instructions telling the agent exactly how to
  perform the behaviour. Cite the digest numbers in the rationale.
- A MEMORY when the digest reveals a durable fact about a user (e.g. their dominant
  usage pattern or preference that future conversations should know). Keep facts
  short and self-contained, and set user_id to the user it belongs to.
- A SOUL when a whole recurring ROLE would help (a reusable specialist, e.g. a
  "data analyst" or "frontend designer" that keeps coming up) — 'create' a new one,
  or 'refine' an existing one (set action='refine' and its key) when the digest shows
  it underperforming or its scope drifting. A soul is a ROLE, not a one-off task.

You are given the EXISTING SKILLS and EXISTING SOULS — never propose one that
duplicates an existing entry; prefer refining a soul over forging a near-duplicate.

Be conservative — propose nothing unless the evidence is strong. **Doing nothing is a
valid and common outcome**: when the window is thin, noisy, or already well-served by the
existing skills/souls, return empty skills/souls/memories lists (just the summary). Do not
invent improvements to look busy. Never propose secrets or anything containing credentials.
Return only the structured report.
"""


def _context_block(existing_skills: list[str], existing_souls: list[str]) -> str:
    """Render the existing skills/souls so the analyst can dedupe + decide refine vs create."""
    skills = "\n".join(f"- {s}" for s in existing_skills) or "- (none)"
    souls = "\n".join(f"- {s}" for s in existing_souls) or "- (none)"
    return f"EXISTING SKILLS:\n{skills}\n\nEXISTING SOULS:\n{souls}"


def build_analyst(
    model: str,
    provider: str = "gemini",
    use_oauth: bool = False,
    *,
    existing_skills: list[str] | None = None,
    existing_souls: list[str] | None = None,
) -> LlmAgent:
    """Build the analyst ADK agent (pure decision; structured output).

    ``existing_skills`` / ``existing_souls`` are short ``"id — description"`` lines folded
    into the instruction so the analyst avoids duplicates and can choose refine vs create.
    """
    from google.adk.agents import LlmAgent

    from gaia.models import resolve_model

    instruction = _INSTRUCTION
    if existing_skills is not None or existing_souls is not None:
        instruction = (
            f"{_INSTRUCTION}\n\n{_context_block(existing_skills or [], existing_souls or [])}"
        )
    return LlmAgent(
        name=NAME,
        model=resolve_model(model, provider=provider, use_oauth=use_oauth),
        description="Mines the usage digest into skill / soul / memory proposals.",
        instruction=instruction,
        output_schema=AnalysisReport,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )
