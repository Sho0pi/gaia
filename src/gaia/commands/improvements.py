"""``/improvements`` — show the skill/soul changes gaia has made (from git history)."""

from __future__ import annotations

from gaia.commands.base import Command, CommandContext


class ImprovementsCommand(Command):
    name = "improvements"
    summary = "List the skills/souls gaia changed (its own learning history)."
    aliases = ("improved",)

    async def run(self, ctx: CommandContext) -> str:
        from gaia.state import StateRepo

        entries = [e for e in StateRepo().entries() if not e.reverted]
        if not entries:
            return "I haven't changed any skills or souls yet."
        lines = [f"- {e.subject} ({e.sha})" for e in entries[:15]]
        return "What I've changed (skills/souls):\n" + "\n".join(lines)
