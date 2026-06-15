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
from typing import TYPE_CHECKING, Any

from gaia.logs import log_event
from gaia.missions.notify import notify_approval, notify_result
from gaia.missions.present import present_result
from gaia.missions.store import Task, TaskStatus, TaskStore
from gaia.souls.run import SoulRun, decide_soul, execute_decision

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
            lines.append("Files: " + ", ".join(dep.artifacts))
        blocks.append("\n".join(lines))
    return "Completed dependencies you can build on:\n\n" + "\n\n".join(blocks)


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
        gated = set(self._gaia.config.missions.approval_classes)
        for task in self._store.ready_tasks():
            if free <= 0:
                break
            if task.id in self._inflight:
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
        return await execute_decision(self._gaia, decision, soul_input, user_id, state=state)

    def _finish(self, task: Task, run: SoulRun) -> None:
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
            self._store.post_result(task.id, run.summary, run.files)  # → done
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
