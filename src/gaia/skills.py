"""Load Anthropic/ADK-style skills and attach them to agents.

A *skill* is a folder ``<skills_dir>/<id>/SKILL.md`` (YAML frontmatter + markdown
body). We reuse ADK's native loader (:func:`google.adk.skills.load_skill_from_dir`)
rather than parsing ``SKILL.md`` ourselves — it returns a ``Skill`` whose
``.instructions`` is the body and ``.frontmatter`` carries name/description. ADK
enforces that the folder name matches the frontmatter ``name``.

This module is the single resolver both call sites use: the factory (dynamic
``AgentSpec.skills``) and the root Gaia agent (config-bound skills). Attaching a skill
*always-on* means appending its instructions to the agent's system prompt, so the
behaviour can't be skipped — distinct from ADK's ``SkillToolset`` progressive
disclosure where the model loads skills on demand (a planned follow-up).

The ADK import is deferred so importing gaia stays cheap and unit tests need no
model backend (the loader itself is pure file parsing).
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from gaia import constants

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.skills import Skill
    from google.adk.tools.base_toolset import BaseToolset

    from gaia.config import GaiaConfig

logger = logging.getLogger(__name__)


def resolve_skills_dir(config: GaiaConfig, *, default: Path = constants.SKILLS_DIR) -> Path:
    """Where skills are loaded from: ``config.skills_dir`` if set, else the default."""
    configured = config.skills_dir
    if configured is not None and str(configured) not in ("", "."):
        return Path(configured)
    return default


def load_skill(skills_dir: Path, skill_id: str) -> Skill | None:
    """Load one skill by id, or ``None`` if its folder is missing/invalid.

    Missing is expected and non-fatal: an id may name a skill that hasn't been
    downloaded yet, so we warn and skip rather than raise.
    """
    from google.adk.skills import load_skill_from_dir

    try:
        return load_skill_from_dir(Path(skills_dir) / skill_id)
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("skill %r not loaded from %s: %s", skill_id, skills_dir, exc)
        return None


def build_skill_toolset(skills_dir: Path) -> BaseToolset | None:
    """Build an ADK ``SkillToolset`` exposing every skill under ``skills_dir`` on demand.

    Returns a toolset that gives the model the progressive-disclosure tools
    (``list_skills`` / ``load_skill`` / ``load_skill_resource``), so an agent can discover
    and pull in a skill's instructions only when a task needs them — distinct from the
    always-on injection of :func:`attach_skills`. ``None`` when the folder is missing or
    holds no valid skill, so callers never attach an empty toolset. A malformed skill is
    skipped (warned), never fatal. ADK is imported lazily (heavy-deps convention).
    """
    skills_dir = Path(skills_dir)
    if not skills_dir.is_dir():
        return None
    from google.adk.skills import list_skills_in_dir
    from google.adk.tools.skill_toolset import SkillToolset

    skills: list[Skill] = []
    for skill_id in list_skills_in_dir(skills_dir):
        skill = load_skill(skills_dir, skill_id)  # warns + returns None on a bad folder
        if skill is not None:
            skills.append(skill)
    if not skills:
        return None
    return SkillToolset(skills=skills)


def attach_skills(base_instruction: str, skill_ids: list[str], skills_dir: Path) -> str:
    """Return ``base_instruction`` with each resolved skill's instructions appended.

    Skills are appended in the given order under a labelled separator. Unknown ids
    are skipped (see :func:`load_skill`). With no skills the base is returned as-is.
    """
    sections = [base_instruction]
    for skill_id in skill_ids:
        skill = load_skill(skills_dir, skill_id)
        if skill is not None:
            sections.append(f"# Skill: {skill.frontmatter.name}\n\n{skill.instructions}")
    return "\n\n".join(sections)


def skill_id_for(name: str) -> str:
    """Normalize a proposed skill name into a kebab-case folder id."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "skill"


def write_skill(skills_dir: Path, name: str, description: str, instructions: str) -> Path:
    """Create ``skills_dir/<id>/SKILL.md`` and validate it loads; return the folder.

    ADK requires the folder name to equal the frontmatter ``name``, so both come from
    :func:`skill_id_for`. Refuses to overwrite an existing skill. The written folder is
    round-tripped through ADK's loader — on failure it is removed and the error raised,
    so a half-written skill can never break startup loading.
    """
    skill_id = skill_id_for(name)
    folder = Path(skills_dir) / skill_id
    if folder.exists():
        raise FileExistsError(f"skill {skill_id!r} already exists at {folder}")

    front = yaml.safe_dump(
        {"name": skill_id, "description": description.strip()}, sort_keys=False, allow_unicode=True
    ).strip()
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\n{front}\n---\n\n{instructions.strip()}\n")

    if load_skill(skills_dir, skill_id) is None:  # warns with the underlying reason
        shutil.rmtree(folder, ignore_errors=True)
        raise ValueError(f"written skill {skill_id!r} failed ADK validation — removed")
    return folder
