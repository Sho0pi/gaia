"""``/memories`` — list what Gaia remembers about the user."""

from __future__ import annotations

from gaia.commands.base import Command, CommandContext


class MemoriesCommand(Command):
    name = "memories"
    agent_access = "user"
    aliases = ("memory",)
    summary = "List what Gaia remembers about you long-term."

    async def run(self, ctx: CommandContext) -> str:
        service = ctx.gaia.memory_service
        if service is None:
            return "Long-term memory is off."
        items = await service.list_memories(user_id=ctx.user_id)
        if not items:
            return "I don't remember anything about you yet."
        return "I remember:\n" + "\n".join(f"- {item}" for item in items)
