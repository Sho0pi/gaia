"""The ``manage_permission`` tool: let an admin grant/revoke ACL capabilities by chat.

Root-only and bound to the live :class:`~gaia.core.agent.Gaia` (like ``message_user`` /
``delegate_to_soul``) because it mutates the user store. The change takes effect on the
target's next turn — the dynamic :class:`~gaia.core.acl_toolset.AclToolset` re-reads
capabilities each turn, so no handler rebuild is needed. Self-gated: the call is refused
unless the *caller* (``tool_context.user_id``) holds ``manage_users`` — so even though the
tool is attached to every root agent, only an admin can actually use it. Tools never raise
to the model; refusals come back as an error dict.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools._helpers import err, ok

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia
    from gaia.users import User, UserStore

#: Tool id / ADK tool name (matches the closure name).
NAME = "manage_permission"


def _resolve_user(store: UserStore, ref: str) -> User | None:
    """Resolve ``ref`` (canonical id, display name, or ``channel:sender``) to a user."""
    user = store.get(ref)
    if user is None:
        user = next((u for u in store.list() if u.name.lower() == ref.lower()), None)
    if user is None and ":" in ref:
        ch, _, sender = ref.partition(":")
        user = store.resolve(ch, sender)
    return user


def make_manage_permission(gaia: Gaia) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the root-only ``manage_permission`` tool bound to ``gaia``."""

    async def manage_permission(
        user: str, action: str, capability: str, *, tool_context: ToolContext
    ) -> dict[str, Any]:
        """Grant or revoke an ACL capability for a user (admin only).

        Use this when an admin asks you to change someone's permissions in chat — e.g.
        "let alice run shell commands" -> action="grant", capability="shell". A capability
        is a group name (web, memory, browser, shell, tasks, cron, manage_users), the
        wildcard "*", or a single tool id. List them with the /acl command.

        Args:
            user: who to change — a user id ("alice"), display name, or "channel:sender".
            action: "grant" or "revoke".
            capability: the capability group, "*", or raw tool id to grant/revoke.
        """
        from gaia.acl import MANAGE_USERS, can

        caller_id = getattr(tool_context, "user_id", None)
        caller = gaia.users.get(caller_id) if caller_id else None
        # caller is None only off the dispatch path (cron/cli/tests) — trusted there.
        if caller is not None and not can(caller, MANAGE_USERS, gaia.config):
            return err("only an admin can manage permissions")

        user, action, capability = (
            user or "",
            action or "",
            capability or "",
        )  # a model may send null, not the default
        act = action.strip().lower()
        if act not in ("grant", "revoke"):
            return err("action must be 'grant' or 'revoke'")
        cap = capability.strip()
        if not cap:
            return err("capability must not be empty")

        target = _resolve_user(gaia.users, user.strip())
        if target is None:
            return err(f"no user matching {user.strip()!r}")

        updated = (
            gaia.users.grant(target.id, cap)
            if act == "grant"
            else gaia.users.revoke(target.id, cap)
        )
        assert updated is not None
        return ok(
            user=updated.id,
            action=act,
            capability=cap,
            grants=updated.grants,
            denies=updated.denies,
        )

    return manage_permission
