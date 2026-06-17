"""The ``run_command`` tool: let Gaia run an in-chat slash command for the user.

Slash commands are normally human-only (handled out-of-band, never seen by the model). This
tool bridges the model to that surface so Gaia can, e.g., install a skill the user asked for
(``run_command("skill", "install <url>")``). Two gates apply, both must pass:

1. the command's :attr:`~gaia.commands.base.Command.agent_access` tier — ``"none"`` is
   refused, ``"admin"`` only when the caller is an admin, ``"user"`` always; and
2. the command's own ACL check inside ``run`` (e.g. ``require_manage_users``),

so a prompt-injected message can't make Gaia run anything the *person* couldn't, and the
dangerous commands stay off unless an admin is driving. Root-only (like ``message_user``);
needs the live handler for handler-dependent commands (``/reset``). Never raises — returns
the standard tool dict.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from google.adk.tools.tool_context import ToolContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia
    from gaia.core.handler import GaiaHandler

NAME = "run_command"


def make_run_command(
    gaia: Gaia, handler: GaiaHandler | None
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the root-only ``run_command`` tool bound to ``gaia`` (+ the live handler)."""

    async def run_command(
        command: str, args: str = "", *, tool_context: ToolContext
    ) -> dict[str, Any]:
        """Run an in-chat command for the current user (e.g. manage skills).

        Use this to act on the user's command surface — most importantly skills:
        run_command("skill", "search caveman"), run_command("skill", "install <git-url>"),
        run_command("skill", "list"). You can only run commands available to you; dangerous
        ones (managing users/permissions) need an admin user.

        Args:
            command: the command name without the leading slash (e.g. "skill", "status").
            args: the rest of the command line (e.g. "install https://...").
        """
        from gaia.commands import CommandContext, default_registry

        name = command.strip().lstrip("/").lower()
        registry = default_registry(gaia.config)
        cmd = registry.get(name)
        if cmd is None:
            return {"status": "error", "error_message": f"unknown command {name!r}"}

        user_id = getattr(tool_context, "user_id", None) or "gaia"
        user = gaia.users.get(user_id)
        role = user.role if user is not None else "admin"  # unresolved caller is trusted

        access = cmd.agent_access
        if access == "none":
            return {
                "status": "error",
                "error_message": f"the {name!r} command isn't available to me",
            }
        if access == "admin" and role != "admin":
            return {
                "status": "error",
                "error_message": f"{name!r} needs an admin — I can't run it for this user",
            }
        if handler is None and name in ("reset", "forget"):
            return {"status": "error", "error_message": f"{name!r} isn't available here"}

        ctx = CommandContext(
            args=args.strip(),
            gaia=gaia,
            handler=handler,  # type: ignore[arg-type]  # None only for handler-free callers, gated above
            registry=registry,
            user_id=user_id,
            session_id=getattr(tool_context, "session_id", None) or "gaia",
            role=role,
        )
        try:
            reply = await cmd.run(ctx)
        except Exception as exc:  # tools never raise to the model
            return {"status": "error", "error_message": f"command failed: {exc}"}
        return {"status": "success", "command": name, "reply": reply}

    return run_command
