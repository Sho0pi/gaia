"""``save_skill`` — write a reusable SKILL from a task gaia just figured out (root-only).

The in-chat learn-and-grow bridge. When gaia finishes a novel multi-step task, it offers to save;
on the user's yes it calls this with the steps that worked, and they're written as a skill (reusing
:func:`gaia.skills.write_skill`, which validates the frontmatter and round-trips ADK's loader).
The new skill is discoverable next turn via the skill toolset (``list_skills`` / ``load_skill``).

Root-only (attached in :meth:`gaia.core.agent.Gaia.build_root_agent`): skills are global, so the
orchestrator owns authoring them, not souls. Distinct from the ``skill_author`` *research*
agent (``gaia skill new --from``): here gaia writes the technique it just used directly — it has the
fresh, working context, so there's nothing to re-research.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools._helpers import err, ok

#: Tool id (matches the closure name).
NAME = "save_skill"


def make_save_skill(skills_dir: Path) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the root-only ``save_skill`` tool, writing into ``skills_dir``."""

    async def save_skill(
        name: str, description: str, instructions: str, *, tool_context: ToolContext
    ) -> dict[str, Any]:
        """Save a reusable skill so you can do this task in one step next time.

        Call this only after the user agrees to save a task as a skill. Capture the steps that
        ACTUALLY worked — the exact tools, commands, and gotchas — as concise markdown instructions,
        not a transcript.

        Args:
            name: a short skill name, e.g. 'download video'.
            description: one line describing when this skill applies.
            instructions: the markdown steps to follow when it applies.
        """
        from gaia.skills import write_skill

        if not name.strip() or not instructions.strip():
            return err("name and instructions are required")
        try:
            folder = write_skill(skills_dir, name, description, instructions)
        except (FileExistsError, ValueError) as exc:
            return err(str(exc))
        return ok(skill_id=folder.name, path=str(folder))

    return save_skill
