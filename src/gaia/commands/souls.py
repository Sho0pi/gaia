"""``/souls`` — list the specialist subagents (souls) Gaia has learned."""

from __future__ import annotations

from gaia.commands.base import Command, CommandContext


class SoulsCommand(Command):
    name = "souls"
    summary = "List the specialist subagents (souls) Gaia has learned."

    async def run(self, ctx: CommandContext) -> str:
        names = ctx.gaia.known_souls()
        if not names:
            return "No souls learned yet."
        return "Souls:\n" + "\n".join(f"- {name}" for name in names)
