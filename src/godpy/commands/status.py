"""``/status`` — a one-glance summary of God's configuration."""

from __future__ import annotations

from godpy.commands.base import Command, CommandContext


class StatusCommand(Command):
    name = "status"
    aliases = ("stats",)
    summary = "Show model, memory settings, and registered counts."

    async def run(self, ctx: CommandContext) -> str:
        cfg = ctx.god.config
        model = cfg.llm.model or ctx.god.settings.model
        mem = cfg.memory
        memory_line = (
            f"on (auto_ingest={mem.auto_ingest}, batch={mem.ingest_batch_size})"
            if mem.enabled
            else "off"
        )
        return (
            f"model: {model}\n"
            f"memory: {memory_line}\n"
            f"subagents: {len(ctx.god.known_souls())}\n"
            f"tools: {len(ctx.god.tools.names())}\n"
            f"commands: {len(ctx.registry.all())}"
        )
