"""``/whoami`` — show the current user/session and runtime state."""

from __future__ import annotations

from gaia.commands.base import Command, CommandContext


class WhoamiCommand(Command):
    name = "whoami"
    summary = "Show your user/session id, model, and memory state."

    async def run(self, ctx: CommandContext) -> str:
        cfg = ctx.gaia.config
        model = cfg.llm.model or ctx.gaia.settings.model
        memory = "on" if cfg.memory.enabled else "off"
        user = ctx.gaia.users.get(ctx.user_id)
        identities = ", ".join(user.identities) if user else "—"
        name = (user.name if user and user.name else "") or ctx.user_id
        return (
            f"user: {ctx.user_id} ({name})\n"
            f"role: {ctx.role}\n"
            f"channels: {identities}\n"
            f"session: {ctx.session_id}\n"
            f"model: {model}\n"
            f"long-term memory: {memory}"
        )
