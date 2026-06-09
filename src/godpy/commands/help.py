"""``/help`` — list the available commands."""

from __future__ import annotations

from godpy.commands.base import Command, CommandContext

NAME = "help"
SUMMARY = "Show this list of commands."


async def run(ctx: CommandContext) -> str:
    lines = [cmd.help_line() for cmd in ctx.registry.all()]
    return "Commands:\n" + "\n".join(lines)


COMMAND = Command(NAME, SUMMARY, run)
