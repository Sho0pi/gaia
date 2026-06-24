"""The ``/task`` command: see and gate the missions task board from chat.

Lists your open tasks (everything not yet ``done``/``failed``), grouped by mission. Scoped
to the caller (per-user, #142); an admin sees every owner's board. ``/task approve <id>``
releases a task parked in ``awaiting_approval`` (a gated class — spend/book/…) back onto the
board; ``/task reject <id>`` fails it and tells the owner.
"""

from __future__ import annotations

from gaia.commands.base import Command, CommandContext
from gaia.missions import CLOSED, TaskStatus, TaskStore


class TasksCommand(Command):
    name = "task"
    summary = "List your open missions/tasks. Usage: /task [approve|reject|answer <id> …]."
    usage = "[approve|reject <id> | answer <id> <text>]"

    async def run(self, ctx: CommandContext) -> str:
        store = ctx.gaia.tasks  # the DI-shared board the dispatcher polls
        verb, _, rest = ctx.args.strip().partition(" ")
        if verb == "answer":
            return self._answer(ctx, store, rest.strip())
        if verb in {"approve", "reject"}:
            return await self._decide(ctx, store, verb, rest.strip())

        # Admins see the whole board; everyone else sees only their own tasks.
        owner = None if ctx.role == "admin" else ctx.user_id
        tasks = [t for t in store.list(owner=owner) if t.status not in CLOSED]
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

    async def _decide(self, ctx: CommandContext, store: TaskStore, verb: str, task_id: str) -> str:
        """Approve (→ inbox) or reject (→ failed) a task parked in ``awaiting_approval``."""
        if not task_id:
            return f"Usage: /task {verb} <id>"
        task = store.get(task_id)
        if task is None or (ctx.role != "admin" and task.owner != ctx.user_id):
            return f"No task {task_id!r}."  # unknown or not yours
        if task.status is not TaskStatus.AWAITING_APPROVAL:
            return f"Task {task_id} isn't awaiting approval (it's {task.status.value})."
        if verb == "approve":
            # Consume the gate so the dispatcher doesn't re-park it on the next poll, then
            # release to inbox for normal dispatch.
            task.approval_class = ""
            task.status = TaskStatus.INBOX
            store.update(task)
            return f"Approved — task {task_id} will run."
        store.update_status(task_id, TaskStatus.FAILED)
        from gaia.missions.notify import notify_rejected

        await notify_rejected(ctx.gaia, task)
        return f"Rejected — task {task_id} won't run."

    def _answer(self, ctx: CommandContext, store: TaskStore, rest: str) -> str:
        """Answer a background mission paused on ``ask_user`` (P3) → the dispatcher resumes it."""
        task_id, _, answer = rest.partition(" ")
        task_id, answer = task_id.strip(), answer.strip()
        if not task_id or not answer:
            return "Usage: /task answer <id> <your answer>"
        task = store.get(task_id)
        if task is None or (ctx.role != "admin" and task.owner != ctx.user_id):
            return f"No task {task_id!r}."  # unknown or not yours
        if task.status is not TaskStatus.AWAITING_INPUT:
            return f"Task {task_id} isn't waiting on you (it's {task.status.value})."
        # Record the answer + release to inbox; the dispatcher's next poll resumes the soul.
        task.pending_answer = answer
        task.status = TaskStatus.INBOX
        store.update(task)
        return f"Got it — task {task_id} will continue with your answer."
