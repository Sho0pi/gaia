"""``/agents`` — list the specialist subagents Gaia has learned."""

from __future__ import annotations

from gaia.commands.base import Command, CommandContext


class AgentsCommand(Command):
    name = "agents"
    summary = "List the specialist subagents Gaia has learned."

    async def run(self, ctx: CommandContext) -> str:
        names = ctx.gaia.known_souls()
        if not names:
            return "No specialist subagents learned yet."
        return "Subagents:\n" + "\n".join(f"- {name}" for name in names)
