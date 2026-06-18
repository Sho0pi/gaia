"""``/improvements`` — show what the self-improve loop has changed (read-only)."""

from __future__ import annotations

import time

from gaia.commands.base import Command, CommandContext


class ImprovementsCommand(Command):
    name = "improvements"
    summary = "List the skills/souls/memories gaia improved on its own."
    aliases = ("improved",)

    async def run(self, ctx: CommandContext) -> str:
        from gaia.analysis.journal import ImprovementJournal

        entries = [e for e in ImprovementJournal().entries() if not e.reverted]
        if not entries:
            return "I haven't made any self-improvements yet."
        lines = []
        for e in entries[-15:]:
            when = time.strftime("%Y-%m-%d", time.localtime(e.ts))
            lines.append(f"- [{when}] {e.action} {e.type}: {e.target} ({e.id})")
        return "What I've improved on my own:\n" + "\n".join(lines)
