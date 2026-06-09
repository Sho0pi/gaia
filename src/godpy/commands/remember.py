"""``/remember <text>`` — store a fact in long-term memory now."""

from __future__ import annotations

from godpy.commands.base import Command, CommandContext


class RememberCommand(Command):
    name = "remember"
    summary = "Save a fact to long-term memory."
    usage = "<fact>"

    async def run(self, ctx: CommandContext) -> str:
        fact = ctx.args.strip()
        if not fact:
            return "Usage: /remember <fact>"
        service = ctx.god.memory_service
        if service is None:
            return "Long-term memory is off — nothing to remember."

        from google.adk.memory.memory_entry import MemoryEntry
        from google.genai import types

        from godpy import constants

        entry = MemoryEntry(content=types.Content(parts=[types.Part(text=fact)]), author="user")
        await service.add_memory(app_name=constants.APP_NAME, user_id=ctx.user_id, memories=[entry])
        return f"Got it — I'll remember: {fact}"
