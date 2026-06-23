"""The mission dispatcher — the one thing awake that runs the board (missions P2).

A poll loop in the daemon: pick ready tasks (inbox/blocked whose dependencies are done),
run each on a soul via the shared :func:`gaia.souls.run.execute_decision` core, post the
result + artifacts back on the board, and notify the originating chat. Dependents unblock
on their own — once a task is ``done``, the next poll finds the tasks it was blocking.

The **result hand-off** is the point of the board: when a task has finished dependencies,
their results + artifact paths are folded into its soul input, so T2 builds on T1's output.

Lifecycle mirrors :class:`gaia.cron.scheduler.CronScheduler` (``start`` / ``stop``), runs on
the daemon's live loop, and uses ``gaia.connectors`` for proactive delivery. Crash recovery
on start resets any ``running`` task (interrupted by a previous shutdown) back to ``inbox``.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gaia.core.elicit import soul_pending_from_json, soul_pending_to_json
from gaia.logs import log_event
from gaia.missions.notify import notify_approval, notify_ask_user, notify_paused, notify_result
from gaia.missions.present import present_result
from gaia.missions.store import Task, TaskStatus, TaskStore
from gaia.souls.run import SoulRun, decide_soul, execute_decision, resume_soul
from gaia.souls.smith import SoulDecision

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia

logger = logging.getLogger(__name__)


def _format_upstream(deps: list[Task]) -> str:
    """Render finished dependencies as a context block to prepend to a task's input."""
    if not deps:
        return ""
    blocks = []
    for dep in deps:
        lines = [f"### {dep.title or dep.id} (task {dep.id})", dep.result or "(no result text)"]
        if dep.artifacts:
            # The files themselves are copied into this task's workspace (see _upstream_files),
            # so the soul opens them as relative names — this just tells it they're there.
            lines.append("Files (now in your workspace): " + ", ".join(dep.artifacts))
        blocks.append("\n".join(lines))
    return "Completed dependencies you can build on:\n\n" + "\n\n".join(blocks)


def _upstream_files(deps: list[Task]) -> list[str]:
    """Absolute paths of finished dependencies' artifacts, to copy into the dependent's workspace.

    A dependency edge hands its files to the next step (the async twin of a delegation
    attachment): resolve each dep's relative artifacts against the workspace it ran in.
    """
    return [
        str(Path(dep.workspace) / name) for dep in deps if dep.workspace for name in dep.artifacts
    ]


class MissionDispatcher:
    """Polls the board and runs ready tasks on souls, bounded by ``max_concurrent``."""

    def __init__(
        self,
        gaia: Gaia,
        *,
        store: TaskStore | None = None,
        max_concurrent: int = 3,
        poll_seconds: float = 2.0,
    ) -> None:
        self._gaia = gaia
        self._store = store if store is not None else gaia.tasks  # the DI-shared board
        self._poll_seconds = poll_seconds
        self._max_concurrent = max_concurrent
        self._sem = asyncio.Semaphore(max_concurrent)
        self._inflight: set[str] = set()
        self._loop_task: asyncio.Task[None] | None = None
        self._workers: set[asyncio.Task[None]] = set()

    def start(self) -> None:
        """Recover interrupted tasks, then spawn the poll loop on the running loop."""
        self.recover()
        self._loop_task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop polling and await in-flight workers (best-effort)."""
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)

    def recover(self) -> None:
        """Reset any task left ``running`` by a previous (crashed) run back to ``inbox``."""
        for task in self._store.list(status=TaskStatus.RUNNING):
            self._store.update_status(task.id, TaskStatus.INBOX)
            log_event("mission_recovered", task=task.id)
            logger.info("recovered interrupted task %s → inbox", task.id)

    async def _run_loop(self) -> None:
        while True:
            try:
                self._dispatch_ready()
            except Exception:  # pragma: no cover - the loop must never die
                logger.exception("dispatcher poll failed")
            await asyncio.sleep(self._poll_seconds)

    def _dispatch_ready(self) -> None:
        """Claim up to the free capacity of ready tasks (skipping in-flight) and spawn workers.

        Capacity is bounded here (not just by the execution semaphore) so we don't flip a
        whole backlog to ``running`` at once — only what can actually run soon.
        """
        free = self._max_concurrent - len(self._inflight)
        if free <= 0:
            return
        missions = self._gaia.config.missions
        gated = set(missions.approval_classes)
        for task in self._store.ready_tasks():
            if free <= 0:
                break
            if task.id in self._inflight:
                continue
            # Per-mission wall-clock budget: past it, pause the whole mission and ask.
            if missions.max_hours > 0 and self._over_budget(task, missions.max_hours):
                continue
            # Approval gate: a task in a gated class parks for a human before it runs. It
            # leaves the ready set (ready_tasks ignores awaiting_approval) until /tasks
            # approve releases it → inbox. Doesn't consume a worker slot.
            if task.approval_class and task.approval_class in gated:
                self._store.update_status(task.id, TaskStatus.AWAITING_APPROVAL)
                log_event("task_awaiting_approval", task=task.id, klass=task.approval_class)
                self._spawn(notify_approval(self._gaia, task))
                continue
            self._inflight.add(task.id)
            self._store.update_status(task.id, TaskStatus.RUNNING)
            worker = asyncio.create_task(self._run_task(task.id))
            self._workers.add(worker)
            worker.add_done_callback(self._workers.discard)
            free -= 1

    def _over_budget(self, task: Task, max_hours: float) -> bool:
        """True if ``task``'s mission has exceeded its wall-clock budget.

        On the first breach the mission root is parked in ``awaiting_approval`` and the user
        is asked; subsequent ready tasks of the same (now-paused) mission just skip — the
        root no longer dispatches and its remaining tasks wait behind the human's call.
        """
        from datetime import datetime

        root = self._store.get(task.mission_id) or task
        try:
            age_seconds = (datetime.now() - datetime.fromisoformat(root.created_at)).total_seconds()
        except ValueError:  # pragma: no cover - corrupt timestamp
            return False
        if age_seconds / 3600 <= max_hours:
            return False
        already = {TaskStatus.AWAITING_APPROVAL, TaskStatus.DONE, TaskStatus.FAILED}
        if root.status not in already:
            self._store.update_status(root.id, TaskStatus.AWAITING_APPROVAL)
            log_event("mission_paused", task=root.id, reason="max_hours")
            self._spawn(notify_paused(self._gaia, root, f"time budget {max_hours:g}h reached"))
        return True

    async def _run_task(self, task_id: str) -> None:
        async with self._sem:
            task = self._store.get(task_id)
            if task is None:  # pragma: no cover - removed between claim and run
                self._inflight.discard(task_id)
                return
            log_event("task_dispatched", task=task.id, owner=task.owner)
            try:
                run = await self._execute(task)
            except Exception as exc:  # the worker must never crash the loop
                logger.exception("task %s crashed", task.id)
                run = SoulRun(False, task.assignee, "", False, error=str(exc))
            self._finish(task, run)
            self._inflight.discard(task_id)

    async def _execute(self, task: Task) -> SoulRun:
        # A task answered after a mid-run ask_user pause (P3) resumes the soul rather than
        # starting a fresh smith decision — see _resume_execute for the hybrid (exact vs re-run).
        if task.pending and task.pending_answer:
            return await self._resume_execute(task)

        deps = [d for d in (self._store.get(b) for b in task.blocked_by) if d is not None]
        upstream = _format_upstream(deps)
        # Re-run-with-results: a parent re-dispatched after its subtasks finished gets its
        # spec + the notes it saved before yielding + the subtasks' results (the upstream
        # block, since the children are now in blocked_by). Stateless, restart-proof.
        parts = [task.spec]
        if task.notes:
            parts.append(f"Your earlier notes:\n{task.notes}")
        if upstream:
            parts.append(upstream)
        soul_input = "\n\n".join(p for p in parts if p).strip()
        user_id = task.owner or "gaia"
        decision = await decide_soul(self._gaia, soul_input)
        # Seed the soul's session so its task tools know which task they're running — a
        # subtask it files is linked to this task (P3 parent re-dispatch).
        state = {"task_id": task.id, "owner": task.owner, "mission_id": task.mission_id}
        # Carry the dependencies' files into this step's workspace (not just their paths as
        # text — the soul is sandboxed and couldn't read another step's dir).
        attachments = _upstream_files(deps)
        return await execute_decision(
            self._gaia, decision, soul_input, user_id, attachments=attachments, state=state
        )

    async def _resume_execute(self, task: Task) -> SoulRun:
        """Resume a soul that paused on ``ask_user`` (P3), now that the user has answered.

        Hybrid: if the warm session is still live in this process, resume the exact paused run
        (keeps its workspace + progress). After a restart it's gone, so re-run the *same* soul
        in its *same* workspace with the Q&A folded into the prompt — the files survive on disk
        and the parked state survives in the db, so the answer is never lost.
        """
        pending = soul_pending_from_json(task.pending)
        answer = task.pending_answer
        if self._gaia.soul_sessions.has(pending.warm_key):
            return await resume_soul(self._gaia, pending, answer)

        log_event("task_resume_cold", task=task.id, soul=pending.soul_key)
        soul_input = "\n\n".join(
            p
            for p in (
                task.spec,
                f"Your earlier notes:\n{task.notes}" if task.notes else "",
                f"Earlier you asked the user: {pending.question}\n"
                f"They answered: {answer}\nContinue from your workspace using that answer.",
            )
            if p
        )
        decision = SoulDecision(
            action="reuse", reason="resume after restart", soul_key=pending.soul_key
        )
        state = {"task_id": task.id, "owner": task.owner, "mission_id": task.mission_id}
        return await execute_decision(
            self._gaia,
            decision,
            soul_input,
            task.owner or "gaia",
            project=pending.project,
            state=state,
        )

    def _finish(self, task: Task, run: SoulRun) -> None:
        if run.pending is not None:
            # The soul asked the user mid-run (first time, or a follow-up during a resume).
            # Park the task durably, pin the warm session for an in-process resume, and ask
            # out-of-band. The user replies via /tasks answer, which re-dispatches here.
            task.pending = soul_pending_to_json(run.pending)
            task.pending_answer = ""
            task.status = TaskStatus.AWAITING_INPUT
            self._store.update(task)
            self._gaia.soul_sessions.pin(run.pending.warm_key)
            log_event("task_awaiting_input", task=task.id, soul=run.pending.soul_key)
            self._spawn(
                notify_ask_user(self._gaia, task, run.pending.question, run.pending.options)
            )
            return
        if task.pending:  # a resume just finished — release the pinned session, clear the slot
            self._gaia.soul_sessions.unpin(soul_pending_from_json(task.pending).warm_key)
            task.pending = ""
            task.pending_answer = ""
            self._store.update(task)
        self._finish_terminal(task, run)

    def _finish_terminal(self, task: Task, run: SoulRun) -> None:
        if run.ok:
            # A soul may have filed subtasks and yielded; don't complete the parent — block
            # it on those children so ready_tasks re-dispatches it (with their results) once
            # they finish. Its summary is saved as notes (the re-run input).
            children = self._store.children(task.id, open_only=True)
            if children:
                task.assignee = run.soul_key
                task.blocked_by = sorted({*task.blocked_by, *(c.id for c in children)})
                task.notes = (task.notes + "\n" + run.summary).strip()
                task.status = TaskStatus.BLOCKED
                self._store.update(task)
                log_event("task_blocked_on_children", task=task.id, children=len(children))
                return  # re-dispatched (re-run-with-results) when the subtasks complete
            task.assignee = run.soul_key
            self._store.update(task)  # persist assignee
            # Persist the workspace too, so a dependent can resolve + copy these artifacts in.
            self._store.post_result(task.id, run.summary, run.files, run.workspace)  # → done
            log_event("task_completed", task=task.id, soul=run.soul_key)
        else:
            task.notes = (task.notes + f"\n[failed] {run.error}").strip()
            task.status = TaskStatus.FAILED
            self._store.update(task)
            log_event("task_failed", task=task.id, error=run.error)
        # Deliver only the mission's *deliverables*: a task that feeds another is an internal
        # step (its result stays on the board). A finished deliverable is *presented* by Gaia
        # (opens + screenshots it); a failure is a short text notice so nothing is silent.
        fresh = self._store.get(task.id) or task
        if run.ok and not self._store.has_dependents(task.id):
            deliver = present_result(self._gaia, fresh, run)
        elif not run.ok:
            deliver = notify_result(self._gaia, fresh, run)
        else:
            return  # internal step — its result feeds a dependent, no user delivery
        self._spawn(deliver)

    def _spawn(self, coro: Any) -> None:
        """Run a best-effort side task (delivery/notify) tracked so it isn't GC'd."""
        task = asyncio.create_task(coro)
        self._workers.add(task)
        task.add_done_callback(self._workers.discard)
