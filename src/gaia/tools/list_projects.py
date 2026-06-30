"""``list_projects`` — show a soul's existing projects (slug + one-line description).

Root-only (like ``delegate_to_soul``): before asking a soul to *change/extend* an app, Gaia can
call this to see what projects the soul already has and reuse the matching one, instead of
inventing a new name that forks a fresh workspace. Reads only each project's ``PROJECT.md``
frontmatter (cheap).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from gaia import constants
from gaia.tools._helpers import ok

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia

#: Tool id / ADK tool name (matches the closure name).
NAME = "list_projects"


def make_list_projects(gaia: Gaia) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the root-only ``list_projects`` tool bound to ``gaia``."""

    async def list_projects(soul: str = "") -> dict[str, Any]:
        """List a soul's existing projects so you reuse the right one, not start a fork.

        Before delegating a CHANGE to an app a soul already built (a tweak, a new page, a fix),
        call this and pass the matching project slug to delegate_to_soul — don't invent a new name
        for an existing app. Each entry is ``{project, description}``.

        Args:
            soul: the soul's key (e.g. "frontend_designer"); omit to list every soul's projects.
        """
        from gaia.souls.run import _existing_projects

        keys = [soul.strip()] if soul.strip() else gaia.known_souls()
        souls = {
            key: [{"project": slug, "description": desc} for slug, desc in projects]
            for key in keys
            if (projects := _existing_projects(constants.AGENTS_DIR, key))
        }
        return ok(souls=souls)

    return list_projects
