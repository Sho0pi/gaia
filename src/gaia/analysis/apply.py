"""Apply an :class:`AnalysisReport` autonomously, with audit + reversibility rails.

The self-improve loop's write side. For each proposal it writes a real artifact — a skill
(researched + written by the :mod:`gaia.agents.skill_author`), a soul (forged or refined via
the :class:`~gaia.agents.registry.SoulRegistry`), or a long-term memory — and records it in
the :class:`~gaia.analysis.journal.ImprovementJournal`. **Additive only**: it never deletes,
de-dupes against the journal (and existing skills/souls), and a refined soul keeps a ``.bak``
so any change is one ``gaia improvements revert`` away. Best-effort: one failing proposal is
logged and skipped, never aborting the rest.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from gaia.analysis.journal import Improvement, ImprovementJournal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.analysis.analyst import AnalysisReport
    from gaia.core.agent import Gaia

logger = logging.getLogger(__name__)


async def apply_report(
    gaia: Gaia,
    report: AnalysisReport,
    *,
    user_id: str | None = None,
    journal: ImprovementJournal | None = None,
) -> list[Improvement]:
    """Apply every proposal in ``report``; return the improvements recorded (in order)."""
    journal = journal or ImprovementJournal()
    applied: list[Improvement] = []

    for memory in report.memories:
        target = memory.user_id or user_id or "gaia"
        imp = await _apply_memory(gaia, target, memory.fact)
        if imp is not None:
            applied.append(journal.record(imp))

    for skill in report.skills:
        imp = await _apply_skill(gaia, skill, journal)
        if imp is not None:
            applied.append(journal.record(imp))

    for soul in report.souls:
        imp = _apply_soul(gaia, soul, journal)
        if imp is not None:
            applied.append(journal.record(imp))

    return applied


async def _apply_memory(gaia: Gaia, user_id: str, fact: str) -> Improvement | None:
    service = gaia.memory_service
    if service is None or not fact.strip():
        return None
    from google.adk.memory.memory_entry import MemoryEntry
    from google.genai import types

    from gaia import constants

    try:
        entry = MemoryEntry(content=types.Content(parts=[types.Part(text=fact)]), author="user")
        await service.add_memory(app_name=constants.APP_NAME, user_id=user_id, memories=[entry])
    except Exception as exc:
        logger.warning("improve: memory write failed: %s", exc)
        return None
    return Improvement(type="memory", target=user_id, action="added", summary=fact[:120])


async def _apply_skill(
    gaia: Gaia, skill: object, journal: ImprovementJournal
) -> Improvement | None:
    from gaia.skills import list_skill_ids, resolve_skills_dir, skill_id_for, write_skill

    name = getattr(skill, "name", "")
    skill_id = skill_id_for(name)
    skills_dir = resolve_skills_dir(gaia.config)
    if skill_id in list_skill_ids(skills_dir) or skill_id in journal.applied_targets("skill"):
        return None  # additive + de-duped: never overwrite an existing/already-added skill

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
    return Improvement(type="skill", target=skill_id, action="created", summary=description[:120])


def _apply_soul(gaia: Gaia, soul: object, journal: ImprovementJournal) -> Improvement | None:
    from gaia.agents.spec import AgentSpec, slugify

    action = str(getattr(soul, "action", "create")).lower()
    name = getattr(soul, "name", "")
    description = getattr(soul, "description", "")
    instruction = getattr(soul, "instruction", "")
    registry = gaia.souls

    if action == "refine":
        key = getattr(soul, "key", "") or slugify(name)
        if registry.get(key) is None:
            return None
        updated = registry.update(key, description=description, instruction=instruction)
        if updated is None:
            return None
        return Improvement(type="soul", target=key, action="refined", summary=description[:120])

    # create — additive + de-duped: don't forge over an existing/already-added soul
    key = slugify(name)
    if registry.get(key) is not None or key in journal.applied_targets("soul"):
        return None
    spec = AgentSpec(
        name=name, description=description, instruction=instruction, model=gaia.config.llm.model
    )
    registry.save(spec)
    return Improvement(type="soul", target=spec.key, action="created", summary=description[:120])


def revert_improvement(
    improvement_id: str,
    *,
    skills_dir: object,
    registry: object,
    journal: ImprovementJournal | None = None,
) -> str:
    """Undo one applied improvement; return a human status line. Marks the journal entry.

    skill (created) -> remove the folder; soul (created) -> delete it; soul (refined) ->
    restore from its ``.bak``. A memory write can't be cleanly un-extracted from mem0, so it
    is only marked reverted. ``skills_dir``/``registry`` are passed so the CLI need not build
    a full Gaia.
    """
    import shutil
    from pathlib import Path

    journal = journal or ImprovementJournal()
    imp = journal.get(improvement_id)
    if imp is None:
        return f"no improvement {improvement_id!r}"
    if imp.reverted:
        return f"{improvement_id} already reverted"

    if imp.type == "skill" and imp.action == "created":
        shutil.rmtree(Path(str(skills_dir)) / imp.target, ignore_errors=True)
    elif imp.type == "soul" and imp.action == "created":
        registry.delete(imp.target)  # type: ignore[attr-defined]
    elif imp.type == "soul" and imp.action == "refined":
        bak = Path(str(registry.directory)) / f"{imp.target}.md.bak"  # type: ignore[attr-defined]
        if bak.exists():
            bak.with_suffix("").write_text(bak.read_text())  # restore <key>.md from .bak
    elif imp.type == "memory":
        journal.mark_reverted(improvement_id)
        return f"marked {imp.type} {imp.target} reverted (memory writes can't be un-done in mem0)"

    journal.mark_reverted(improvement_id)
    return f"reverted {imp.action} {imp.type}: {imp.target}"
