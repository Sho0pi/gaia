"""The skill author: a one-shot LLM agent that drafts a SKILL from a one-line brief.

Parallel to :func:`gaia.souls.smith.build_soul_smith` (which authors an ``AgentSpec``):
this authors a skill's ``description`` + markdown ``instructions`` via structured output.
Lives here (not in the CLI) so it's reusable — ``gaia skill new --from`` today, the
self-improve loop's skill proposals later. ADK is imported lazily (heavy-deps convention).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.config import GaiaConfig

_INSTRUCTION = (
    "You write a SKILL for an AI agent — concise, reusable prompt knowledge (NOT code). "
    "Given a skill name and a one-line brief, return a one-sentence description and a "
    "markdown instructions body the agent should follow when the skill applies. Be specific "
    "and actionable; no preamble."
)


def draft_skill(cfg: GaiaConfig, name: str, brief: str) -> tuple[str, str]:
    """Draft ``(description, instructions)`` for a skill from a brief (needs a model key)."""
    import asyncio

    return asyncio.run(_draft_skill_async(cfg, name, brief))


async def _draft_skill_async(cfg: GaiaConfig, name: str, brief: str) -> tuple[str, str]:
    from google.adk.agents import LlmAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    from pydantic import BaseModel

    from gaia import constants
    from gaia.models import resolve_model

    class SkillDraft(BaseModel):
        description: str
        instructions: str

    author = LlmAgent(
        name="skill_author",
        model=resolve_model(
            cfg.llm.model, provider=cfg.llm.provider, use_oauth=cfg.llm.openai.use_oauth
        ),
        description="Authors a reusable skill (prompt knowledge) from a brief.",
        instruction=_INSTRUCTION,
        output_schema=SkillDraft,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )
    session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
    await session_service.create_session(
        app_name=constants.APP_NAME, user_id="cli", session_id="skill-draft"
    )
    runner = Runner(app_name=constants.APP_NAME, agent=author, session_service=session_service)
    content = types.Content(
        role="user", parts=[types.Part(text=f"Skill name: {name}\nBrief: {brief}")]
    )
    parts: list[str] = []
    try:
        async for event in runner.run_async(
            user_id="cli", session_id="skill-draft", new_message=content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                parts.extend(p.text for p in event.content.parts if p.text)
    finally:
        await runner.close()  # type: ignore[no-untyped-call]
    draft = SkillDraft.model_validate_json("".join(parts))
    return draft.description, draft.instructions
