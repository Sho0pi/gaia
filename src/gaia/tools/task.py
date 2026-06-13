"""The ``task_*`` tools: Gaia files work on the durable task board (missions P1).

Five focused ADK function tools (not one action-dispatch tool) so each carries a tight,
self-documenting schema the model reasons about: create / list / get / update / complete.
All share one :class:`~gaia.missions.TaskStore` (the ~/.gaia/tasks.db board) and return
the ADK dict shape; none raise to the model, none self-log (``ToolLoggingPlugin`` does).

**Per-user scoping (#142):** every task is owned by the human that asked — read from the
live invocation (``tool_context._invocation_context.user_id``, the same per-user key the
memory tools use). Listing and lookups are confined to the caller's own tasks, so one
person's missions stay private on a shared channel. ``created_by`` is fixed to ``gaia`` in
P1 (souls get these tools in P3).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.missions import Task, TaskStatus, TaskStore

#: Tool ids / ADK tool names (match the closure names).
TASK_CREATE = "task_create"
TASK_LIST = "task_list"
TASK_GET = "task_get"
TASK_UPDATE = "task_update"
TASK_COMPLETE = "task_complete"


def _err(message: str) -> dict[str, Any]:
    return {"status": "error", "error_message": message}


def _owner(tool_context: ToolContext) -> str:
    """The human user_id behind this turn (the per-user task scope)."""
    return getattr(getattr(tool_context, "_invocation_context", None), "user_id", "") or ""


def _split(csv: str) -> list[str]:
    """A comma-separated tool arg → a clean list (ADK schemas favour flat scalars)."""
    return [part.strip() for part in csv.split(",") if part.strip()]


def _owned(store: TaskStore, task_id: str, owner: str) -> Task | None:
    """The task, only if it belongs to ``owner`` — otherwise ``None`` (treated as absent)."""
    task = store.get(task_id)
    return task if task is not None and task.owner == owner else None


def make_task_create(store: TaskStore) -> Callable[..., dict[str, Any]]:
    """Return the ``task_create`` tool bound to ``store``."""

    def task_create(
        title: str,
        spec: str = "",
        mission_id: str = "",
        parent_id: str = "",
        blocked_by: str = "",
        approval_class: str = "",
        *,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """File a new task on the board (status ``inbox``). Use this to track real work —
        a deliverable, a long job, a step that must wait for another — that should survive
        beyond this turn.

        Args:
            title: a short label for the task.
            spec: the full instruction for whoever will run it.
            mission_id: the mission this belongs to; omit to start a new mission.
            parent_id: the parent task id when this is a subtask.
            blocked_by: comma-separated task ids that must finish first.
            approval_class: spend | book | send_as_me | destructive — if it needs a human ok.
        """
        if not title.strip():
            return _err("title must not be empty")
        depth = 0
        if parent_id:
            parent = store.get(parent_id)
            if parent is None:
                return _err(f"no parent task {parent_id!r}")
            depth = parent.depth + 1
            mission_id = mission_id or parent.mission_id
        task = store.create(
            Task(
                title=title.strip(),
                spec=spec,
                mission_id=mission_id,
                parent_id=parent_id,
                depth=depth,
                blocked_by=_split(blocked_by),
                approval_class=approval_class.strip(),
                owner=_owner(tool_context),
                created_by="gaia",
            )
        )
        return {"status": "success", "task": task.model_dump(mode="json")}

    return task_create


def make_task_list(store: TaskStore) -> Callable[..., dict[str, Any]]:
    """Return the ``task_list`` tool bound to ``store``."""

    def task_list(
        mission_id: str = "", status: str = "", *, tool_context: ToolContext
    ) -> dict[str, Any]:
        """List your tasks, optionally filtered by mission or status.

        Args:
            mission_id: only tasks in this mission.
            status: only tasks in this status (inbox/assigned/running/blocked/
                awaiting_approval/review/done/failed).
        """
        parsed: TaskStatus | None = None
        if status:
            try:
                parsed = TaskStatus(status)
            except ValueError:
                return _err(f"unknown status {status!r}")
        tasks = store.list(mission=mission_id or None, status=parsed, owner=_owner(tool_context))
        return {"status": "success", "tasks": [t.model_dump(mode="json") for t in tasks]}

    return task_list


def make_task_get(store: TaskStore) -> Callable[..., dict[str, Any]]:
    """Return the ``task_get`` tool bound to ``store``."""

    def task_get(task_id: str, *, tool_context: ToolContext) -> dict[str, Any]:
        """Get one of your tasks by id (full detail).

        Args:
            task_id: the task to fetch.
        """
        task = _owned(store, task_id, _owner(tool_context))
        if task is None:
            return _err(f"no task {task_id!r}")
        return {"status": "success", "task": task.model_dump(mode="json")}

    return task_get


def make_task_update(store: TaskStore) -> Callable[..., dict[str, Any]]:
    """Return the ``task_update`` tool bound to ``store``."""

    def task_update(
        task_id: str,
        status: str = "",
        notes: str = "",
        assignee: str = "",
        *,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Update one of your tasks — change its status, append notes, or set an assignee.

        Args:
            task_id: the task to update.
            status: a new status (inbox/assigned/running/blocked/awaiting_approval/
                review/done/failed).
            notes: free-text progress notes (replaces the existing notes).
            assignee: the soul/agent now responsible.
        """
        task = _owned(store, task_id, _owner(tool_context))
        if task is None:
            return _err(f"no task {task_id!r}")
        if status:
            try:
                task.status = TaskStatus(status)
            except ValueError:
                return _err(f"unknown status {status!r}")
        if notes:
            task.notes = notes
        if assignee:
            task.assignee = assignee
        store.update(task)
        return {"status": "success", "task": task.model_dump(mode="json")}

    return task_update


def make_task_complete(store: TaskStore) -> Callable[..., dict[str, Any]]:
    """Return the ``task_complete`` tool bound to ``store``."""

    def task_complete(
        task_id: str, result: str = "", artifacts: str = "", *, tool_context: ToolContext
    ) -> dict[str, Any]:
        """Mark one of your tasks done and record its result + artifacts.

        Args:
            task_id: the task to complete.
            result: a short summary of the outcome.
            artifacts: comma-separated workspace paths the task produced.
        """
        if _owned(store, task_id, _owner(tool_context)) is None:
            return _err(f"no task {task_id!r}")
        task = store.post_result(task_id, result, _split(artifacts))
        assert task is not None  # ownership check above proved it exists
        return {"status": "success", "task": task.model_dump(mode="json")}

    return task_complete
