"""The ``run_command`` tool: let Gaia run an in-chat slash command for the user.

Slash commands are normally human-only (handled out-of-band, never seen by the model). This
tool bridges the model to that surface so Gaia can, e.g., install a skill the user asked for
(``run_command("skill install <url>")``). It applies the **same ACL gate** as the human
``/cmd`` path — :func:`gaia.commands.base.authorize`: the agent may run a command iff the
caller holds its ``capability`` (a command with no capability is open). No separate "agent"
tier — the agent acts as the user, exactly like the tool ACL. So a prompt-injected message
can't make Gaia run anything the *person* couldn't (a user's agent can't ``/grant``; an
admin's can). Root-only (like ``message_user``); needs the live handler for
handler-dependent commands (``/reset``). Never raises — returns the standard tool dict.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools._helpers import err, ok

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

        Pass the whole command line as ``command`` — e.g.
        run_command("skill install https://github.com/acme/skills"),
        run_command("skill search caveman"), run_command("skill list"). (Splitting it into
        command + args also works.) You can only run commands available to you; dangerous
        ones (managing users/permissions) need an admin user.

        Args:
            command: the command line (command name + its arguments), with or without a
                leading slash, e.g. "skill install <git-url>".
            args: optional extra arguments, appended to ``command``.
        """
        from gaia.commands import CommandContext, authorize, default_registry

        # The model usually passes the whole line in `command`; split off the first token
        # as the command name and treat the rest (+ any `args`) as the arguments.
        command, args = command or "", args or ""  # a model may send null, not the default
        name, _, inline = command.strip().lstrip("/").partition(" ")
        name = name.lower()
        full_args = " ".join(p for p in (inline.strip(), args.strip()) if p)
        registry = default_registry(gaia.config)
        cmd = registry.get(name)
        if cmd is None:
            return err(f"unknown command {name!r}")

        user_id = getattr(tool_context, "user_id", None) or "gaia"
        user = gaia.users.get(user_id)
        role = user.role if user is not None else "admin"  # unresolved caller is trusted

        if handler is None and name in ("reset", "forget"):
            return err(f"{name!r} isn't available here")

        ctx = CommandContext(
            args=full_args,
            gaia=gaia,
            handler=handler,  # type: ignore[arg-type]  # None only for handler-free callers, gated above
            registry=registry,
            user_id=user_id,
            session_id=getattr(tool_context, "session_id", None) or "gaia",
            role=role,
        )
        # Same ACL gate as the human /cmd path: the agent may run a command iff the user
        # holds its capability (no capability = open). No separate agent tier.
        if refusal := authorize(cmd, ctx):
            return err(refusal)
        try:
            reply = await cmd.run(ctx)
        except Exception as exc:  # tools never raise to the model
            return err(f"command failed: {exc}")
        return ok(command=name, reply=reply)

    return run_command
