"""The ``set_communication_style`` tool — let Gaia change its own voice on request.

When the user asks in natural language ("talk like a caveman", "be more human"), the agent
calls this to persist the global ``default_communication_style`` in gaia.yaml. The handler
rebuilds on the next turn (it re-reads gaia.yaml every message), so the new voice takes effect
from the next reply.

Admin-only: it changes the *global* voice, so it's in the ``manage_users`` ACL group
(``acl/groups.py``) — only admins' agents are handed the tool, matching the ``/style`` command gate.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from gaia.communication import STYLES, set_style
from gaia.tools._helpers import err, ok

#: Tool id (matches the closure name so ADK names the tool the same).
NAME = "set_communication_style"


def make_set_communication_style() -> Callable[..., Any]:
    """Return the ADK ``set_communication_style`` tool (writes the canonical gaia.yaml)."""

    async def set_communication_style(style: str) -> dict[str, Any]:
        """Set Gaia's communication style (voice). Takes effect from the next message.

        Args:
            style: one of "human" (natural, casual), "caveman" (ultra-terse), or "ai" (raw model).
        """
        from gaia import constants

        chosen = style.strip().lower()
        try:
            set_style(constants.CONFIG_PATH, chosen)
        except ValueError:
            return err(f"unknown style {style!r}; choose from {', '.join(STYLES)}")
        return ok(style=chosen, note="in effect from your next message")

    return set_communication_style
