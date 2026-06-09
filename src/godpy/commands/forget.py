"""``/forget`` — wipe the user's long-term memory (destructive, confirm-gated)."""

from __future__ import annotations

from godpy.commands.base import Command, CommandContext

NAME = "forget"
SUMMARY = "Wipe your long-term memory. Send '/forget yes' to confirm."
USAGE = "[yes]"

#: Token the user must send to actually wipe memory.
_CONFIRM = "yes"


async def run(ctx: CommandContext) -> str:
    service = ctx.god.memory_service
    if service is None:
        return "Long-term memory is off — nothing to forget."

    if ctx.args.strip().lower() != _CONFIRM:
        count = len(await service.list_memories(user_id=ctx.user_id))
        if count == 0:
            return "I don't remember anything about you yet."
        return (
            f"This permanently wipes ALL long-term memory about you ({count} item"
            f"{'s' if count != 1 else ''}). Send '/forget yes' to confirm."
        )

    removed = await service.forget(user_id=ctx.user_id)
    return f"Forgot everything ({removed} item{'s' if removed != 1 else ''})."


COMMAND = Command(NAME, SUMMARY, run, usage=USAGE)
