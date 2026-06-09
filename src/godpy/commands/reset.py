"""``/reset`` — clear this conversation's short-term context."""

from __future__ import annotations

from godpy.commands.base import Command, CommandContext

NAME = "reset"
ALIASES = ("clear", "new")
SUMMARY = "Start fresh: clear this conversation (keeps long-term memory)."


async def run(ctx: CommandContext) -> str:
    # Persist anything buffered for long-term first, then drop the live session so the
    # next message starts a brand-new ADK conversation with no prior turns.
    await ctx.handler.flush()
    ctx.handler.reset_session()
    return "Conversation cleared. I've kept what I remember about you long-term."


COMMAND = Command(NAME, SUMMARY, run, ALIASES)
