"""``/whoami`` — show the current user/session and runtime state."""

from __future__ import annotations

from godpy.commands.base import Command, CommandContext


class WhoamiCommand(Command):
    name = "whoami"
    summary = "Show your user/session id, model, and memory state."

    async def run(self, ctx: CommandContext) -> str:
        cfg = ctx.god.config
        model = cfg.llm.model or ctx.god.settings.model
        memory = "on" if cfg.memory.enabled else "off"
        return (
            f"user: {ctx.user_id}\n"
            f"session: {ctx.session_id}\n"
            f"model: {model}\n"
            f"long-term memory: {memory}"
        )
