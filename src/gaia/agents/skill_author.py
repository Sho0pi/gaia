"""The skill author: a tool-using agent that researches, then writes a SKILL.

Parallel to :func:`gaia.souls.smith.build_soul_smith` (which authors an ``AgentSpec``), but
this one is a *researcher*: it can web-search + fetch to get the real technique, read the
existing skills folder to avoid duplicating / match house style, and recall long-term
memory to fit the user's patterns — then write the skill. (A structured ``output_schema``
can't be used here: ADK forbids tools alongside it, so instead we pin the output format in
the instruction and parse it.) Reused by ``gaia skill new --from`` and, later, the
self-improve loop's skill proposals. ADK imported lazily (heavy-deps convention).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia

#: Tools the author may use when present in the registry (memory tool added separately).
_RESEARCH_TOOLS = ("web_search", "web_fetch", "fs_read", "load_memory")

_INSTRUCTION = (
    "You author a SKILL for an AI agent: concise, reusable prompt KNOWLEDGE (how to do a "
    "thing well), not code. Work in this order:\n"
    "1. Discover the existing skills (list_skills) and read a couple relevant ones "
    "(load_skill, or fs_read in the skills folder) to reuse their knowledge, match the house "
    "style, and avoid duplicating one.\n"
    "2. If the topic needs real, current detail (an API, a technique, a format), web_search "
    "and web_fetch to ground it — don't invent specifics.\n"
    "3. If load_memory is available, recall the user's relevant preferences and fold them in.\n"
    "Then output ONLY the skill, in EXACTLY this format and nothing else (no preamble, no "
    "code fences):\n"
    "<one-sentence description on the first line>\n"
    "\n"
    "<the markdown instructions body the agent should follow when this skill applies>"
)


def draft_skill(gaia: Gaia, name: str, brief: str, *, user_id: str = "gaia") -> tuple[str, str]:
    """Research + author ``(description, instructions)`` for a skill (needs a model key)."""
    import asyncio

    return asyncio.run(_draft_skill_async(gaia, name, brief, user_id=user_id))


async def _draft_skill_async(gaia: Gaia, name: str, brief: str, *, user_id: str) -> tuple[str, str]:
    from google.adk.agents import LlmAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from gaia import constants
    from gaia.models import resolve_model
    from gaia.skills import resolve_skills_dir

    cfg = gaia.config
    available = set(gaia.tools.names())
    tools: list[Any] = [gaia.tools.get(t) for t in _RESEARCH_TOOLS if t in available]
    # The on-demand skill toolset (list_skills / load_skill / load_skill_resource): lets the
    # author discover and read existing skills to reuse their knowledge and match house style.
    tools.extend(gaia.container.skill_toolsets())

    skills_dir = resolve_skills_dir(cfg)
    author = LlmAgent(
        name="skill_author",
        model=resolve_model(
            cfg.llm.model, provider=cfg.llm.provider, use_oauth=cfg.llm.openai.use_oauth
        ),
        description="Researches and writes a reusable skill (prompt knowledge).",
        instruction=f"{_INSTRUCTION}\n\nThe skills folder is: {skills_dir}",
        tools=tools,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )
    session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
    await session_service.create_session(
        app_name=constants.APP_NAME, user_id=user_id, session_id="skill-draft"
    )
    runner = Runner(
        app_name=constants.APP_NAME,
        agent=author,
        session_service=session_service,
        memory_service=gaia.memory_service,  # enables load_memory
    )
    content = types.Content(
        role="user", parts=[types.Part(text=f"Skill name: {name}\nBrief: {brief}")]
    )
    parts: list[str] = []
    try:
        async for event in runner.run_async(
            user_id=user_id, session_id="skill-draft", new_message=content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                parts.extend(p.text for p in event.content.parts if p.text)
    finally:
        await runner.close()  # type: ignore[no-untyped-call]
    return _parse_draft("".join(parts), fallback_description=name)


def _parse_draft(text: str, *, fallback_description: str) -> tuple[str, str]:
    """Split the author's output into (description, instructions).

    The instruction pins the format: the first non-empty line is the one-sentence
    description, the rest is the markdown body. Strips stray code fences defensively.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    rows = cleaned.splitlines()
    idx = next((i for i, r in enumerate(rows) if r.strip()), None)
    if idx is None:
        return fallback_description, cleaned
    description = rows[idx].strip().lstrip("#").strip()
    body = "\n".join(rows[idx + 1 :]).strip()
    return description or fallback_description, body or cleaned
