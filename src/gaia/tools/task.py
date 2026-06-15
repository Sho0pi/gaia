"""The ``task_*`` tools: Gaia files work on the durable task board (missions P1).

Five focused ADK function tools (not one action-dispatch tool) so each carries a tight,
self-documenting schema the model reasons about: create / list / get / update / complete.
All share one :class:`~gaia.missions.TaskStore` (the ~/.gaia/tasks.db board) and return
the ADK dict shape; none raise to the model, none self-log (``ToolLoggingPlugin`` does).

**Per-user scoping (#142):** every task is owned by the human that asked — read from the
live invocation via ADK's public ``tool_context.user_id`` (the same per-user key the
memory tools use). Listing and lookups are confined to the caller's own tasks, so one
person's missions stay private on a shared channel. ``created_by`` is fixed to ``gaia`` in
P1 (souls get these tools in P3).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.connectors.base import current_chat
from gaia.missions import Task, TaskStatus, TaskStore

#: Tool ids / ADK tool names (match the closure names).
TASK_CREATE = "task_create"
TASK_LIST = "task_list"
TASK_GET = "task_get"
TASK_UPDATE = "task_update"
TASK_COMPLETE = "task_complete"
TASK_PLAN = "task_plan"


def _err(message: str) -> dict[str, Any]:
    return {"status": "error", "error_message": message}


def _owner(tool_context: ToolContext) -> str:
    """The human user_id behind this turn (the per-user task scope)."""
    return tool_context.user_id or ""


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
        """File task on board (status ``inbox``). Track real work that must
        survive beyond this turn: deliverable, long job, blocked step.

        Args:
            title: short task label.
            spec: full instruction for runner.
            mission_id: mission this belongs to; omit to start new mission.
            parent_id: parent task id when subtask.
            blocked_by: comma-separated task ids that must finish first.
            approval_class: spend | book | send_as_me | destructive — if human ok needed.
        """
        if not title.strip():
            return _err("title must not be empty")
        deps = _split(blocked_by)
        missing = [d for d in deps if store.get(d) is None]
        if missing:
            return _err(
                f"blocked_by references unknown task id(s): {', '.join(missing)}. Create the "
                "upstream task first and use its returned id — or use task_plan to file the "
                "whole mission at once."
            )
        depth = 0
        notify_channel, notify_chat = current_chat.get()  # the chat to answer when done
        if parent_id:
            parent = store.get(parent_id)
            if parent is None:
                return _err(f"no parent task {parent_id!r}")
            depth = parent.depth + 1
            mission_id = mission_id or parent.mission_id
            # A subtask inherits the parent's reply target when filed outside a live chat.
            notify_channel = notify_channel or parent.notify_channel
            notify_chat = notify_chat or parent.notify_chat
        task = store.create(
            Task(
                title=title.strip(),
                spec=spec,
                mission_id=mission_id,
                parent_id=parent_id,
                depth=depth,
                blocked_by=deps,
                approval_class=approval_class.strip(),
                owner=_owner(tool_context),
                created_by="gaia",
                notify_channel=notify_channel,
                notify_chat=notify_chat,
            )
        )
        return {"status": "success", "task": task.model_dump(mode="json")}

    return task_create


def _parse_plan(plan: Any) -> Any:
    """Parse the model's plan arg leniently — JSON, a Python-literal string, or already a list.

    Models often emit single-quoted pseudo-JSON (``[{'ref': ...}]``) or ADK may hand the
    argument through already parsed. Accept all of these; return ``None`` if unparseable.
    """
    if isinstance(plan, list):
        return plan
    if not isinstance(plan, str):
        return None
    text = plan.strip()
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        pass
    try:
        import ast

        return ast.literal_eval(text)  # tolerant of single quotes / Python repr
    except (ValueError, SyntaxError):
        return None


def _order_refs(plan: list[dict[str, Any]]) -> list[str] | str:
    """Topological order of refs, or an error string (unknown ref / duplicate / cycle)."""
    refs = [str(item.get("ref", "")).strip() for item in plan]
    if any(not r for r in refs):
        return "every task needs a non-empty 'ref'"
    if len(set(refs)) != len(refs):
        return "task refs must be unique"
    known = set(refs)
    deps: dict[str, list[str]] = {}
    for item, ref in zip(plan, refs, strict=True):
        edges = [str(d).strip() for d in item.get("depends_on", []) or []]
        for d in edges:
            if d not in known:
                return f"task {ref!r} depends_on unknown ref {d!r}"
            if d == ref:
                return f"task {ref!r} depends on itself"
        deps[ref] = edges

    # Kahn's algorithm — emit refs whose deps are already placed; a leftover means a cycle.
    order: list[str] = []
    placed: set[str] = set()
    while len(order) < len(refs):
        progressed = False
        for ref in refs:
            if ref not in placed and all(d in placed for d in deps[ref]):
                order.append(ref)
                placed.add(ref)
                progressed = True
        if not progressed:
            return "dependency cycle detected in the plan"
    return order


def make_task_plan(store: TaskStore) -> Callable[..., dict[str, Any]]:
    """Return the ``task_plan`` tool bound to ``store`` (Gaia-only — files a whole mission)."""

    def task_plan(plan: str, *, tool_context: ToolContext) -> dict[str, Any]:
        """File whole multi-step mission, with real dependency edges.

        Use when task needs another task's output (chains, fan-out, fan-in).
        Avoid guessing upstream id mid-turn. Dispatcher runs each task on soul;
        task starts when dependencies done; dependency results + files feed in.

        Args:
            plan: JSON array of tasks. Each: {"ref": "<local label>", "title": "...",
                "spec": "<full instruction>", "depends_on": ["<ref>", ...]}. ``depends_on``
                lists local refs (NOT ids) this task waits for; omit for tasks that
                can start now (run in parallel). Example:
                [{"ref":"program","title":"Design the A/B gym program","spec":"..."},
                 {"ref":"site","title":"Build the site","spec":"...","depends_on":["program"]}]
        """
        items = _parse_plan(plan)
        if items is None:
            return _err("plan must be a JSON (or Python-literal) array of task objects")
        if not isinstance(items, list) or not items:
            return _err("plan must be a non-empty array")
        if not all(isinstance(i, dict) and str(i.get("title", "")).strip() for i in items):
            return _err("each task needs at least a 'title'")

        order = _order_refs(items)
        if isinstance(order, str):
            return _err(order)

        by_ref = {str(i["ref"]).strip(): i for i in items}
        mission_id = uuid.uuid4().hex[:8]
        owner = _owner(tool_context)
        notify_channel, notify_chat = current_chat.get()
        ref_to_id: dict[str, str] = {}
        created: list[dict[str, Any]] = []
        for ref in order:  # dependencies first, so blocked_by ids always resolve
            item = by_ref[ref]
            blocked = [ref_to_id[d] for d in (item.get("depends_on") or [])]
            task = store.create(
                Task(
                    title=str(item["title"]).strip(),
                    spec=str(item.get("spec", "")),
                    mission_id=mission_id,
                    depth=(len(blocked) and 1) or 0,
                    blocked_by=blocked,
                    approval_class=str(item.get("approval_class", "")).strip(),
                    owner=owner,
                    created_by="gaia",
                    notify_channel=notify_channel,
                    notify_chat=notify_chat,
                )
            )
            ref_to_id[ref] = task.id
            created.append({"ref": ref, **task.model_dump(mode="json")})
        return {"status": "success", "mission_id": mission_id, "tasks": created}

    return task_plan


def make_task_list(store: TaskStore) -> Callable[..., dict[str, Any]]:
    """Return the ``task_list`` tool bound to ``store``."""

    def task_list(
        mission_id: str = "", status: str = "", *, tool_context: ToolContext
    ) -> dict[str, Any]:
        """List tasks, optional mission/status filter.

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
        """Get task by id (full detail).

        Args:
            task_id: task to fetch.
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
        """Update task: status, notes, or assignee.

        Args:
            task_id: task to update.
            status: new status (inbox/assigned/running/blocked/awaiting_approval/
                review/done/failed).
            notes: free-text progress notes (replaces existing notes).
            assignee: soul/agent now responsible.
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
        """Mark task done; record result + artifacts.

        Args:
            task_id: task to complete.
            result: short outcome summary.
            artifacts: comma-separated workspace paths task produced.
        """
        if _owned(store, task_id, _owner(tool_context)) is None:
            return _err(f"no task {task_id!r}")
        task = store.post_result(task_id, result, _split(artifacts))
        assert task is not None  # ownership check above proved it exists
        return {"status": "success", "task": task.model_dump(mode="json")}

    return task_complete
