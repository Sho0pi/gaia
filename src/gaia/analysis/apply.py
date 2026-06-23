"""Apply an :class:`AnalysisReport` autonomously, versioning each change with git.

The self-improve loop's write side. For each proposal it writes a real artifact — a skill
(researched + written by the :mod:`gaia.agents.skill_author`), a soul (forged or refined via
the :class:`~gaia.agents.registry.SoulRegistry`), or a long-term memory — and, for the file
artifacts (skills/souls), records it as a git commit in the ``~/.gaia`` state repo
(:mod:`gaia.state`). One commit per change = a per-change history that's auditable and
revertable (``gaia improvements revert <sha>``), one change among many included. **Additive
only**: never deletes; de-dupes against what's already on disk. Memory writes go to mem0 (not
files), so they're applied + announced but not in the git history (see ``/memories``).
Best-effort: a failing proposal is logged and skipped, never aborting the rest.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.analysis.analyst import AnalysisReport
    from gaia.core.agent import Gaia

logger = logging.getLogger(__name__)


async def apply_report(
    gaia: Gaia, report: AnalysisReport, *, user_id: str | None = None
) -> list[str]:
    """Apply every proposal; return a human line per applied change (committed where a file)."""
    applied: list[str] = []

    for memory in report.memories:
        target = memory.user_id or user_id or "gaia"
        if await _apply_memory(gaia, target, memory.fact):
            applied.append(f"added memory for {target}")

    for skill in report.skills:
        line = await _apply_skill(gaia, skill)
        if line is not None:
            applied.append(line)

    for soul in report.souls:
        line = _apply_soul(gaia, soul)
        if line is not None:
            applied.append(line)

    return applied


async def _apply_memory(gaia: Gaia, user_id: str, fact: str) -> bool:
    service = gaia.memory_service
    if service is None or not fact.strip():
        return False
    from google.adk.memory.memory_entry import MemoryEntry
    from google.genai import types

    from gaia import constants

    try:
        entry = MemoryEntry(content=types.Content(parts=[types.Part(text=fact)]), author="user")
        await service.add_memory(app_name=constants.APP_NAME, user_id=user_id, memories=[entry])
    except Exception as exc:
        logger.warning("improve: memory write failed: %s", exc)
        return False
    return True


async def _apply_skill(gaia: Gaia, skill: object) -> str | None:
    from gaia.skills import list_skill_ids, resolve_skills_dir, skill_id_for, write_skill
    from gaia.state import commit_change

    name = getattr(skill, "name", "")
    skill_id = skill_id_for(name)
    skills_dir = resolve_skills_dir(gaia.config)
    if skill_id in list_skill_ids(skills_dir):
        return None  # additive: never overwrite an existing skill

    description = getattr(skill, "description", name)
    rationale = getattr(skill, "rationale", "")
    instructions = getattr(skill, "instructions", "")
    # Prefer a researched body from the skill author; fall back to the proposal's text.
    # Await the async author directly — apply runs on the daemon loop, where the sync
    # draft_skill (asyncio.run) would raise.
    try:
        from gaia.agents.skill_author import draft_skill_async

        description, instructions = await draft_skill_async(
            gaia, name, f"{description}. {rationale}"
        )
    except Exception as exc:
        logger.warning("improve: skill author failed, using proposal text: %s", exc)
    if not instructions.strip():
        return None
    try:
        write_skill(skills_dir, name, description, instructions)
    except Exception as exc:
        logger.warning("improve: skill write failed: %s", exc)
        return None
    commit_change(f"improve: created skill '{skill_id}' — {description}", rationale)
    return f"created skill {skill_id}"


def _apply_soul(gaia: Gaia, soul: object) -> str | None:
    from gaia.agents.spec import AgentSpec, slugify
    from gaia.state import commit_change

    action = str(getattr(soul, "action", "create")).lower()
    name = getattr(soul, "name", "")
    description = getattr(soul, "description", "")
    instruction = getattr(soul, "instruction", "")
    rationale = getattr(soul, "rationale", "")
    registry = gaia.souls

    if action == "refine":
        key = getattr(soul, "key", "") or slugify(name)
        if registry.get(key) is None:
            return None
        if registry.update(key, description=description, instruction=instruction) is None:
            return None
        commit_change(f"improve: refined soul '{key}' — {description}", rationale)
        return f"refined soul {key}"

    # create — additive: don't forge over an existing soul
    key = slugify(name)
    if registry.get(key) is not None:
        return None
    spec = AgentSpec(
        name=name, description=description, instruction=instruction, model=gaia.config.llm.model
    )
    registry.save(spec)
    commit_change(f"improve: created soul '{spec.key}' — {description}", rationale)
    return f"created soul {spec.key}"
