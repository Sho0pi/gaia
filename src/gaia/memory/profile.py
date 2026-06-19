"""Session-start user profile: one LLM call that distils what gaia knows about the user.

When a conversation's agent is built (session start), this compresses the user's durable
mem0 facts + their recent projects (the task board) into a short, importance-ranked block
that's injected into the prompt — so Gaia always knows who it's talking to. Importance is
the model's job (keep the name, fold the football chatter into a line), not recency.

No storage, no schedule: it's recomputed each session so it's always fresh. It returns
``None`` (no LLM call) when the user has nothing yet, so a fresh store never triggers a
model call. The ADK import is deferred (heavy-deps rule).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from gaia import constants

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia

logger = logging.getLogger(constants.LOGGER_NAME)

#: How many of the user's most recent tasks count as "recent projects".
_RECENT_PROJECTS = 5

_INSTRUCTION = """\
You compress what gaia knows about ONE user into a short profile it can keep in context.
You are given the user's stored FACTS (already distilled) and their RECENT PROJECTS.

Rules:
- Keep identity-critical facts verbatim: name, key relationships, hard preferences.
- Fold recurring themes into a single line (e.g. many football facts -> "follows football").
- List the user's recent/active projects briefly.
- At most {limit} bullet points total. Plain markdown bullets only — no preamble, no headers.
- If there is genuinely nothing worth keeping, return an empty response.
"""


async def distill_profile(gaia: Gaia, user_id: str) -> str | None:
    """Return a compact profile block for ``user_id`` (one LLM call), or None when empty."""
    service = gaia.memory_service
    if service is None:
        return None
    try:
        facts = await service.list_memories(user_id=user_id)
    except Exception as exc:  # a memory backend without list support must not break a turn
        logger.warning("profile distill: list_memories failed for %s: %s", user_id, exc)
        return None
    projects = _recent_projects(gaia, user_id)
    if not facts and not projects:
        return None  # nothing to profile -> no model call (keeps fresh stores key-free)
    try:
        return await _run_profiler(gaia, facts, projects) or None
    except Exception as exc:  # never break a turn over recall
        logger.warning("profile distill failed for %s: %s", user_id, exc)
        return "\n".join(f"- {fact}" for fact in facts) or None  # fallback: raw facts


def _recent_projects(gaia: Gaia, user_id: str) -> list[str]:
    """The user's most recent tasks as ``title (status)`` lines (newest first)."""
    try:
        tasks = gaia.tasks.list(owner=user_id)[:_RECENT_PROJECTS]
    except Exception:  # pragma: no cover - board read is best-effort
        return []
    return [f"{task.title} ({task.status.value})" for task in tasks if task.title]


async def _run_profiler(gaia: Gaia, facts: list[str], projects: list[str]) -> str:
    """Run the one-shot profiler agent over the facts + projects; return its text."""
    from google.adk.agents import LlmAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from gaia.models import resolve_model

    cfg = gaia.config
    agent = LlmAgent(
        name="profiler",
        model=resolve_model(
            cfg.llm.model or gaia.settings.model,
            provider=cfg.llm.provider,
            use_oauth=cfg.llm.openai.use_oauth,
        ),
        description="Distils a compact user profile from stored facts + recent projects.",
        instruction=_INSTRUCTION.format(limit=cfg.memory.preload_limit),
    )
    session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
    await session_service.create_session(
        app_name=constants.APP_NAME, user_id="profiler", session_id="profile"
    )
    runner = Runner(app_name=constants.APP_NAME, agent=agent, session_service=session_service)
    facts_text = "\n".join(f"- {fact}" for fact in facts) or "(none)"
    projects_text = "\n".join(f"- {project}" for project in projects) or "(none)"
    prompt = f"FACTS:\n{facts_text}\n\nRECENT PROJECTS:\n{projects_text}"
    content = types.Content(role="user", parts=[types.Part(text=prompt)])
    parts: list[str] = []
    try:
        async for event in runner.run_async(
            user_id="profiler", session_id="profile", new_message=content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                parts.extend(part.text for part in event.content.parts if part.text)
    finally:
        await runner.close()  # type: ignore[no-untyped-call]
    return "".join(parts).strip()
