"""``/forget`` — wipe the user's long-term memory (destructive, confirm-gated)."""

from __future__ import annotations

from godpy.commands.base import Command, CommandContext


class ForgetCommand(Command):
    name = "forget"
    summary = "Wipe your long-term memory. Send '/forget yes' to confirm."
    usage = "[yes]"

    #: Token the user must send to actually wipe memory.
    CONFIRM = "yes"

    async def run(self, ctx: CommandContext) -> str:
        service = ctx.god.memory_service
        if service is None:
            return "Long-term memory is off — nothing to forget."

        if ctx.args.strip().lower() != self.CONFIRM:
            count = len(await service.list_memories(user_id=ctx.user_id))
            if count == 0:
                return "I don't remember anything about you yet."
            plural = "s" if count != 1 else ""
            return (
                f"This permanently wipes ALL long-term memory about you ({count} item"
                f"{plural}). Send '/forget yes' to confirm."
            )

        removed = await service.forget(user_id=ctx.user_id)
        plural = "s" if removed != 1 else ""
        return f"Forgot everything ({removed} item{plural})."
