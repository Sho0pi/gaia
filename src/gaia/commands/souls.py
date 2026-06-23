"""``/souls`` — list the specialist subagents (souls) Gaia has learned, and which are live."""

from __future__ import annotations

from gaia.commands.base import Command, CommandContext


def _idle(seconds: float) -> str:
    """A short idle label: ``just now`` / ``5m`` / ``2h``."""
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    return f"{seconds / 3600:.1f}h"


class SoulsCommand(Command):
    name = "souls"
    summary = "List the souls Gaia has learned, and the ones live right now (warm sessions)."

    async def run(self, ctx: CommandContext) -> str:
        names = ctx.gaia.known_souls()
        active = ctx.gaia.soul_sessions.active()  # (soul/project, idle_seconds), warmest first

        parts: list[str] = []
        if names:
            parts.append("Souls:\n" + "\n".join(f"- {name}" for name in names))
        else:
            parts.append("No souls learned yet.")
        if active:
            live = "\n".join(f"- {key} (idle {_idle(idle)})" for key, idle in active)
            parts.append(f"Live now ({len(active)}):\n{live}")
        return "\n\n".join(parts)
