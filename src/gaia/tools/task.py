"""The ``task_*`` tools: Gaia files work on the durable task board (missions P1).

Five focused ADK function tools (not one action-dispatch tool) so each carries a tight,
self-documenting schema the model reasons about: create / list / get / update / complete.
All share one :class:`~gaia.missions.TaskStore` (the ~/.gaia/tasks.db board) and return
the ADK dict shape; none raise to the model, none self-log (``ToolLoggingPlugin`` does).

**Per-user scoping (#142):** every task is owned by the human that asked — read from the
live invocation via ADK's public ``tool_context.user_id`` (the same per-user key the
memory tools use). Listing and lookups are confined to the caller's own tasks, so one
person's missions stay private on a shared channel.

**Souls (P3):** these tools also run inside a soul mid-task. The dispatcher seeds the
soul's session ``state`` (``task_id``/``created_by``); ``task_create`` reads it to file a
subtask of the soul's own task and stamp ``created_by``, and ``task_get``/``update``/
``complete`` default to that task — so a soul can save notes on its own task and file a
subtask without ever learning its own id. Guards: ``max_depth`` (nesting), an ancestry
cycle check, and a per-mission ``max_tasks`` cap that pauses the mission.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia.connectors.base import current_chat
from gaia.missions import Task, TaskStatus, TaskStore
from gaia.tools._helpers import err, ok

#: Tool ids / ADK tool names (match the closure names).
TASK_CREATE = "task_create"
TASK_LIST = "task_list"
TASK_GET = "task_get"
TASK_UPDATE = "task_update"
TASK_COMPLETE = "task_complete"
TASK_PLAN = "task_plan"


def _owner(tool_context: ToolContext) -> str:
    """The human user_id behind this turn (the per-user task scope)."""
    return tool_context.user_id or ""


def _state(tool_context: ToolContext) -> dict[str, Any]:
    """The soul's seeded session state (``task_id``/``created_by``…), or empty for Gaia.

    The dispatcher seeds this when it runs a soul on a board task (see ``souls/run.py``); a
    plain Gaia turn has none, so the tools behave exactly as in P1 there.
    """
    state = getattr(tool_context, "state", None)
    if state is None:
        return {}
    if hasattr(state, "to_dict"):  # ADK's State object (not a plain dict)
        return dict(state.to_dict())
    return dict(state)


def _ancestor_ids(store: TaskStore, task_id: str) -> set[str]:
    """The ids on ``task_id``'s parent chain (excluding itself); visited-guarded vs cycles."""
    seen: set[str] = set()
    current = store.get(task_id) if task_id else None
    while current is not None and current.parent_id and current.parent_id not in seen:
        seen.add(current.parent_id)
        current = store.get(current.parent_id)
    return seen


def _split(csv: str) -> list[str]:
    """A comma-separated tool arg → a clean list (ADK schemas favour flat scalars)."""
    return [part.strip() for part in csv.split(",") if part.strip()]


def _owned(store: TaskStore, task_id: str, owner: str) -> Task | None:
    """The task, only if it belongs to ``owner`` — otherwise ``None`` (treated as absent)."""
    task = store.get(task_id)
    return task if task is not None and task.owner == owner else None


def _pause_mission(store: TaskStore, mission_id: str, note: str) -> None:
    """Park a mission's root task in ``awaiting_approval`` with ``note`` (cap breach)."""
    root = store.get(mission_id)
    if root is None or root.status in {TaskStatus.DONE, TaskStatus.FAILED}:
        return
    root.status = TaskStatus.AWAITING_APPROVAL
    root.notes = f"{root.notes}\n[paused] {note}".strip()
    store.update(root)


def make_task_create(
    store: TaskStore, *, max_depth: int = 3, max_tasks: int = 20
) -> Callable[..., dict[str, Any]]:
    """Return the ``task_create`` tool bound to ``store`` (with the P3 board guards)."""

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
        beyond this turn. When a soul calls this mid-task it is filed as a SUBTASK of the
        soul's current task automatically (it then saves notes and yields; it is re-run with
        the subtask's results once done).

        Args:
            title: a short label for the task.
            spec: the full instruction for whoever will run it.
            mission_id: the mission this belongs to; omit to start a new mission.
            parent_id: the parent task id when this is a subtask (defaults to the caller's
                own task when a soul files it).
            blocked_by: comma-separated task ids that must finish first.
            approval_class: spend | book | send_as_me | destructive — if it needs a human ok.
        """
        # A model may send explicit null for an omitted optional arg (gpt-5.x does; Gemini omits
        # it) — it arrives as None and overrides the "" default, so coerce before the str ops below.
        title, spec, mission_id, parent_id, blocked_by, approval_class = (
            title or "",
            spec or "",
            mission_id or "",
            parent_id or "",
            blocked_by or "",
            approval_class or "",
        )
        if not title.strip():
            return err("title must not be empty")
        state = _state(tool_context)
        # A soul filing a subtask links it to its own task by default; created_by is the
        # soul's key (Gaia turns have no state → parent stays unset, created_by "gaia").
        parent_id = parent_id or state.get("task_id", "")
        created_by = state.get("created_by", "gaia")
        deps = _split(blocked_by)
        missing = [d for d in deps if store.get(d) is None]
        if missing:
            return err(
                f"blocked_by references unknown task id(s): {', '.join(missing)}. Create the "
                "upstream task first and use its returned id — or use task_plan to file the "
                "whole mission at once."
            )
        depth = 0
        notify_channel, notify_chat = current_chat.get()  # the chat to answer when done
        if parent_id:
            parent = store.get(parent_id)
            if parent is None:
                return err(f"no parent task {parent_id!r}")
            depth = parent.depth + 1
            if depth > max_depth:
                return err(f"subtask depth limit reached (max_depth={max_depth})")
            # Cycle guard: a subtask must not wait on one of its own ancestors (deadlock).
            ancestors = _ancestor_ids(store, parent_id) | {parent_id}
            cyclic = [d for d in deps if d in ancestors]
            if cyclic:
                return err(f"blocked_by would create an ancestry cycle: {', '.join(cyclic)}")
            mission_id = mission_id or parent.mission_id
            # A subtask inherits the parent's reply target when filed outside a live chat.
            notify_channel = notify_channel or parent.notify_channel
            notify_chat = notify_chat or parent.notify_chat
        # Per-mission task cap (total ever filed) — breach pauses the mission, asks the user.
        if mission_id and len(store.list(mission=mission_id)) >= max_tasks:
            _pause_mission(store, mission_id, f"task cap reached (max_tasks={max_tasks})")
            return err(f"mission task limit reached (max_tasks={max_tasks}) — mission paused")
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
                created_by=created_by,
                notify_channel=notify_channel,
                notify_chat=notify_chat,
            )
        )
        return ok(task=task.model_dump(mode="json"))

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


def make_task_plan(store: TaskStore, *, max_tasks: int = 20) -> Callable[..., dict[str, Any]]:
    """Return the ``task_plan`` tool bound to ``store`` (Gaia-only — files a whole mission)."""

    def task_plan(plan: str, *, tool_context: ToolContext) -> dict[str, Any]:
        """File a whole multi-step mission at once, wiring real dependency edges.

        Use this for any task that needs another task's output (chains, fan-out, fan-in) —
        it avoids the broken pattern of guessing an upstream id mid-turn. The dispatcher
        runs each task on a soul; a task runs as soon as its dependencies are done, and the
        finished dependencies' results + files are fed into it.

        Args:
            plan: a JSON array of tasks. Each: {"ref": "<local label>", "title": "...",
                "spec": "<full instruction>", "depends_on": ["<ref>", ...]}. ``depends_on``
                lists the local refs (NOT ids) this task waits for; omit it for tasks that
                can start immediately (they run in parallel). Example:
                [{"ref":"program","title":"Design the A/B gym program","spec":"..."},
                 {"ref":"site","title":"Build the site","spec":"...","depends_on":["program"]}]
        """
        items = _parse_plan(plan)
        if items is None:
            return err("plan must be a JSON (or Python-literal) array of task objects")
        if not isinstance(items, list) or not items:
            return err("plan must be a non-empty array")
        if not all(isinstance(i, dict) and str(i.get("title", "")).strip() for i in items):
            return err("each task needs at least a 'title'")
        if len(items) > max_tasks:
            return err(f"plan exceeds the per-mission task cap (max_tasks={max_tasks})")

        order = _order_refs(items)
        if isinstance(order, str):
            return err(order)

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
        return ok(mission_id=mission_id, tasks=created)

    return task_plan


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
                return err(f"unknown status {status!r}")
        tasks = store.list(mission=mission_id or None, status=parsed, owner=_owner(tool_context))
        return ok(tasks=[t.model_dump(mode="json") for t in tasks])

    return task_list


def make_task_get(store: TaskStore) -> Callable[..., dict[str, Any]]:
    """Return the ``task_get`` tool bound to ``store``."""

    def task_get(task_id: str = "", *, tool_context: ToolContext) -> dict[str, Any]:
        """Get one of your tasks by id (full detail).

        Args:
            task_id: the task to fetch (defaults to the caller soul's own task).
        """
        task_id = task_id or _state(tool_context).get("task_id", "")
        task = _owned(store, task_id, _owner(tool_context))
        if task is None:
            return err(f"no task {task_id!r}")
        return ok(task=task.model_dump(mode="json"))

    return task_get


def make_task_update(store: TaskStore) -> Callable[..., dict[str, Any]]:
    """Return the ``task_update`` tool bound to ``store``."""

    def task_update(
        task_id: str = "",
        status: str = "",
        notes: str = "",
        assignee: str = "",
        *,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Update one of your tasks — change its status, append notes, or set an assignee.

        Args:
            task_id: the task to update (defaults to the caller soul's own task — use this to
                save working notes before filing a subtask and yielding).
            status: a new status (inbox/assigned/running/blocked/awaiting_approval/
                review/done/failed).
            notes: free-text progress notes (replaces the existing notes).
            assignee: the soul/agent now responsible.
        """
        task_id = task_id or _state(tool_context).get("task_id", "")
        task = _owned(store, task_id, _owner(tool_context))
        if task is None:
            return err(f"no task {task_id!r}")
        if status:
            try:
                task.status = TaskStatus(status)
            except ValueError:
                return err(f"unknown status {status!r}")
        if notes:
            task.notes = notes
        if assignee:
            task.assignee = assignee
        store.update(task)
        return ok(task=task.model_dump(mode="json"))

    return task_update


def make_task_complete(store: TaskStore) -> Callable[..., dict[str, Any]]:
    """Return the ``task_complete`` tool bound to ``store``."""

    def task_complete(
        task_id: str = "", result: str = "", artifacts: str = "", *, tool_context: ToolContext
    ) -> dict[str, Any]:
        """Mark one of your tasks done and record its result + artifacts.

        Args:
            task_id: the task to complete (defaults to the caller soul's own task).
            result: a short summary of the outcome.
            artifacts: comma-separated workspace paths the task produced.
        """
        result, artifacts = result or "", artifacts or ""  # a model may send null, not the default
        task_id = task_id or _state(tool_context).get("task_id", "")
        if _owned(store, task_id, _owner(tool_context)) is None:
            return err(f"no task {task_id!r}")
        if store.children(task_id, open_only=True):
            return err(
                "task has open subtasks — leave it; it is re-run with their results once "
                "they finish (don't complete it now)"
            )
        task = store.post_result(task_id, result, _split(artifacts))
        assert task is not None  # ownership check above proved it exists
        return ok(task=task.model_dump(mode="json"))

    return task_complete
