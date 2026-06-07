"""Load Anthropic/ADK-style skills and attach them to agents.

A *skill* is a folder ``<skills_dir>/<id>/SKILL.md`` (YAML frontmatter + markdown
body). We reuse ADK's native loader (:func:`google.adk.skills.load_skill_from_dir`)
rather than parsing ``SKILL.md`` ourselves — it returns a ``Skill`` whose
``.instructions`` is the body and ``.frontmatter`` carries name/description. ADK
enforces that the folder name matches the frontmatter ``name``.

This module is the single resolver both call sites use: the factory (dynamic
``AgentSpec.skills``) and the root God agent (config-bound skills). Attaching a skill
*always-on* means appending its instructions to the agent's system prompt, so the
behaviour can't be skipped — distinct from ADK's ``SkillToolset`` progressive
disclosure where the model loads skills on demand (a planned follow-up).

The ADK import is deferred so importing godpy stays cheap and unit tests need no
model backend (the loader itself is pure file parsing).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.skills import Skill

    from godpy.config import GodConfig

logger = logging.getLogger(__name__)

# Default home for manually-placed skills (clawhub auto-download is a follow-up).
DEFAULT_SKILLS_DIR = Path.home() / ".godpy" / "skills"


def resolve_skills_dir(config: GodConfig, *, default: Path = DEFAULT_SKILLS_DIR) -> Path:
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
