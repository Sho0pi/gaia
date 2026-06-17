"""``/status`` — a one-glance summary of Gaia's configuration."""

from __future__ import annotations

from gaia.commands.base import Command, CommandContext


class StatusCommand(Command):
    name = "status"
    agent_access = "user"
    aliases = ("stats",)
    summary = "Show model, memory settings, and registered counts."

    async def run(self, ctx: CommandContext) -> str:
        cfg = ctx.gaia.config
        model = cfg.llm.model or ctx.gaia.settings.model
        mem = cfg.memory
        memory_line = (
            f"on (auto_ingest={mem.auto_ingest}, batch={mem.ingest_batch_size})"
            if mem.enabled
            else "off"
        )
        lines = [
            f"model: {model}",
            f"memory: {memory_line}",
            f"subagents: {len(ctx.gaia.known_souls())}",
            f"tools: {len(ctx.gaia.tools.names())}",
            f"commands: {len(ctx.registry.all())}",
        ]
        missing = ctx.gaia.tools.missing
        if missing:
            disabled = ", ".join(f"{name} ({reason})" for name, reason in sorted(missing.items()))
            lines.append(f"disabled tools: {disabled}")
        return "\n".join(lines)
