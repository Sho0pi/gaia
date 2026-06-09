"""``/agents`` — list the specialist subagents God has learned."""

from __future__ import annotations

from godpy.commands.base import Command, CommandContext

NAME = "agents"
SUMMARY = "List the specialist subagents God has learned."


async def run(ctx: CommandContext) -> str:
    names = ctx.god.known_agents()
    if not names:
        return "No specialist subagents learned yet."
    return "Subagents:\n" + "\n".join(f"- {name}" for name in names)


COMMAND = Command(NAME, SUMMARY, run)
