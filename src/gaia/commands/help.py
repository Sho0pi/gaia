"""``/help`` — list the available commands."""

from __future__ import annotations

from gaia.commands.base import Command, CommandContext


class HelpCommand(Command):
    name = "help"
    agent_access = "user"
    summary = "Show this list of commands."

    async def run(self, ctx: CommandContext) -> str:
        lines = [cmd.help_line() for cmd in ctx.registry.all()]
        return "Commands:\n" + "\n".join(lines)
