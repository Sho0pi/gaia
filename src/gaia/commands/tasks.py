"""The ``/tasks`` command: see the missions task board from chat.

Lists your open tasks (everything not yet ``done``/``failed``), grouped by mission. Scoped
to the caller (per-user, #142); an admin sees every owner's board. ``/tasks approve <id>``
is parsed now but parks until the approval *gates* land in P3 — it returns a clear stub so
the surface is discoverable.
"""

from __future__ import annotations

from gaia.commands.base import Command, CommandContext
from gaia.missions import CLOSED, TaskStore


class TasksCommand(Command):
    name = "tasks"
    summary = "List your open missions/tasks. Usage: /tasks [approve <id>]."
    usage = "[approve <id>]"

    async def run(self, ctx: CommandContext) -> str:
        verb, _, rest = ctx.args.strip().partition(" ")
        if verb == "approve":
            task_id = rest.strip()
            if not task_id:
                return "Usage: /tasks approve <id>"
            return (
                f"Approval gates aren't wired yet (task {task_id!r} stays put) — "
                "coming with the missions engine (P3)."
            )

        # Admins see the whole board; everyone else sees only their own tasks.
        owner = None if ctx.role == "admin" else ctx.user_id
        tasks = [t for t in TaskStore().list(owner=owner) if t.status not in CLOSED]
        if not tasks:
            return "No open tasks. Ask me to track something as tasks and it'll show here."

        by_mission: dict[str, list[str]] = {}
        for t in tasks:
            line = f"  • {t.id} [{t.status.value}] {t.title}"
            if t.blocked_by:
                line += f" (blocked by {', '.join(t.blocked_by)})"
            by_mission.setdefault(t.mission_id or t.id, []).append(line)

        blocks = [f"Mission {mid}:\n" + "\n".join(lines) for mid, lines in by_mission.items()]
        return "Open tasks:\n" + "\n\n".join(blocks)
