"""The missions task board: ``~/.gaia/tasks.db`` (stdlib sqlite3, WAL).

A *task* is one row of the company's state; a *mission* is a root task plus its tree
(``parent_id`` chain). This is the durable blackboard the missions epic builds on (design:
``concepts/missions``): P1 ships a manual board (Gaia files/lists/completes tasks that
survive a restart); the dispatcher, schedules and approval *release* are later phases.

SQLite (not the json the cron/user stores use) because the board is relational — filtered
queries (by mission, status, owner), a ``blocked_by`` dependency check, and concurrent
readers (a ``gaia task`` CLI while a chat turn writes). **WAL** makes that last case safe.
A connection is opened per call (sqlite3 is cheap; no shared mutable singleton), and the
schema is created idempotently with ``CREATE TABLE IF NOT EXISTS`` — no migration framework
until rows in the wild justify one.

``owner`` is the *human* ``user_id`` the mission serves (per-user scoping, like memory,
#142); ``created_by`` is the *agent* that filed the task (gaia / a soul / cron) — kept
distinct for the task tree + audit.

The status vocabulary is gaia's own, but it maps cleanly to A2A's ``TaskState`` (see
:data:`A2A_STATE`) so the future external-agent bridge (P5) is a trivial translation — we
deliberately take no ``a2a`` dependency here.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from gaia import constants

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterator, Sequence


class TaskStatus(StrEnum):
    """The task lifecycle (design doc state diagram).

    ``inbox → assigned → running → blocked → awaiting_approval → review → done|failed``.
    """

    INBOX = "inbox"
    ASSIGNED = "assigned"
    RUNNING = "running"
    BLOCKED = "blocked"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_INPUT = "awaiting_input"  # paused mid-run on ask_user, awaiting the user's answer (P3)
    REVIEW = "review"
    DONE = "done"
    FAILED = "failed"


#: Map of our status → the A2A ``TaskState`` string it corresponds to. Not imported from
#: ``a2a`` (no dependency in P1); this is the contract the P5 embassy bridge will honour.
A2A_STATE: dict[TaskStatus, str] = {
    TaskStatus.INBOX: "submitted",
    TaskStatus.ASSIGNED: "submitted",
    TaskStatus.RUNNING: "working",
    TaskStatus.BLOCKED: "working",
    TaskStatus.AWAITING_APPROVAL: "input_required",  # gated classes → auth_required (P3)
    TaskStatus.AWAITING_INPUT: "input_required",  # a soul paused on ask_user, awaiting an answer
    TaskStatus.REVIEW: "working",
    TaskStatus.DONE: "completed",
    TaskStatus.FAILED: "failed",
}

#: Terminal statuses — a task here is no longer "open".
CLOSED: frozenset[TaskStatus] = frozenset({TaskStatus.DONE, TaskStatus.FAILED})

#: Annotation alias: inside TaskStore the name `list` is the method, not the builtin.
TaskList = list["Task"]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Task(BaseModel):
    """One row on the board. A root task has empty ``parent_id`` and ``depth`` 0."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    mission_id: str = ""  # the root task's id; empty until set (a root is its own mission)
    parent_id: str = ""
    title: str = ""
    spec: str = ""  # the full instruction for whoever runs the task
    status: TaskStatus = TaskStatus.INBOX
    assignee: str = ""  # soul key once dispatched (P2)
    blocked_by: list[str] = Field(default_factory=list)  # task ids that must be done first
    depth: int = 0
    artifacts: list[str] = Field(default_factory=list)  # workspace paths the task produced
    workspace: str = ""  # abs dir the soul ran in, so a dependent can copy these artifacts in
    result: str = ""
    notes: str = ""
    owner: str = ""  # the human user_id this mission serves (per-user scope)
    created_by: str = ""  # the agent that filed it (gaia / soul key / cron)
    approval_class: str = ""  # spend | book | send_as_me | destructive (gate is P3)
    budget_used: float = 0.0
    # Where to deliver the result: the chat that filed the task (captured at creation). Empty
    # falls back to the owner's identity, then the cron.deliver default. (P2 notify.)
    notify_channel: str = ""
    notify_chat: str = ""
    # A soul paused mid-run on ask_user (P3): ``pending`` is the JSON-serialized SoulPending
    # (the question + everything needed to resume), ``pending_answer`` the user's reply once
    # given. Both empty otherwise; cleared when the run resolves.
    pending: str = ""
    pending_answer: str = ""
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


_COLUMNS = (
    "id, mission_id, parent_id, title, spec, status, assignee, blocked_by, depth, "
    "artifacts, result, notes, owner, created_by, approval_class, budget_used, "
    "notify_channel, notify_chat, created_at, updated_at, workspace, pending, pending_answer"
)

#: Columns added after the initial P1 ship — applied to an existing db via idempotent ALTER.
_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("notify_channel", "TEXT NOT NULL DEFAULT ''"),
    ("notify_chat", "TEXT NOT NULL DEFAULT ''"),
    ("workspace", "TEXT NOT NULL DEFAULT ''"),
    ("pending", "TEXT NOT NULL DEFAULT ''"),
    ("pending_answer", "TEXT NOT NULL DEFAULT ''"),
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL DEFAULT '',
    parent_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    spec TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'inbox',
    assignee TEXT NOT NULL DEFAULT '',
    blocked_by TEXT NOT NULL DEFAULT '[]',
    depth INTEGER NOT NULL DEFAULT 0,
    artifacts TEXT NOT NULL DEFAULT '[]',
    result TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    approval_class TEXT NOT NULL DEFAULT '',
    budget_used REAL NOT NULL DEFAULT 0,
    notify_channel TEXT NOT NULL DEFAULT '',
    notify_chat TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    workspace TEXT NOT NULL DEFAULT '',
    pending TEXT NOT NULL DEFAULT '',
    pending_answer TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tasks_mission ON tasks(mission_id);
CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id);
"""


class TaskStore:
    """SQLite-backed task board. A connection is opened per operation (WAL-safe)."""

    def __init__(self, path: Path | None = None) -> None:
        # No disk I/O here — constructing the store is cheap (the registry builds one per
        # Gaia). The db file + schema are created lazily on first use, like CronStore.
        self._path = Path(path) if path is not None else constants.TASKS_DB
        self._ready = False
        self._init_lock = threading.Lock()

    def _ensure_ready(self) -> None:
        """One-time, thread-safe db setup: WAL (persistent), schema + migration.

        Serialized so concurrent threads can't race the schema/ALTER (which would surface
        as 'database is locked'). ``journal_mode=WAL`` is a db-level setting stored in the
        file, so it only needs to run here, not on every connection.
        """
        if self._ready:
            return
        with self._init_lock:
            if self._ready:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._path, isolation_level=None)
            conn.row_factory = sqlite3.Row  # _migrate reads PRAGMA rows by column name
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.executescript(_SCHEMA)  # CREATE IF NOT EXISTS — idempotent
                self._migrate(conn)
            finally:
                conn.close()
            self._ready = True

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self._ensure_ready()
        conn = sqlite3.connect(self._path, isolation_level=None)  # autocommit
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")  # wait out a concurrent writer
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Add post-P1 columns to an existing db (idempotent ALTER; rows preserved)."""
        have = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
        for name, decl in _MIGRATIONS:
            if name not in have:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {decl}")

    # -- writes ----------------------------------------------------------------------

    def create(self, task: Task) -> Task:
        """Insert ``task`` (root tasks become their own mission). Returns the stored task."""
        if not task.mission_id and not task.parent_id:
            task.mission_id = task.id  # a root is its own mission
        task.updated_at = _now()
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO tasks ({_COLUMNS}) "
                f"VALUES ({', '.join(['?'] * len(_COLUMNS.split(',')))})",
                _to_row(task),
            )
        return task

    def update(self, task: Task) -> Task:
        """Persist every field of ``task`` (bumps ``updated_at``)."""
        task.updated_at = _now()
        assignments = ", ".join(f"{c.strip()} = ?" for c in _COLUMNS.split(",")[1:])
        with self._connect() as conn:
            conn.execute(
                f"UPDATE tasks SET {assignments} WHERE id = ?",
                (*_to_row(task)[1:], task.id),
            )
        return task

    def update_status(self, task_id: str, status: TaskStatus) -> Task | None:
        """Set just the status (and ``updated_at``); ``None`` if the task is unknown."""
        task = self.get(task_id)
        if task is None:
            return None
        task.status = status
        return self.update(task)

    def post_result(
        self, task_id: str, result: str, artifacts: Sequence[str] = (), workspace: str = ""
    ) -> Task | None:
        """Record a finished task's ``result`` + ``artifacts`` (+ its ``workspace``) and mark
        it ``done`` — the workspace lets a dependent resolve these artifacts to copy them in."""
        task = self.get(task_id)
        if task is None:
            return None
        task.result = result
        task.artifacts = list(artifacts)
        if workspace:
            task.workspace = workspace
        task.status = TaskStatus.DONE
        return self.update(task)

    # -- reads -----------------------------------------------------------------------

    def get(self, task_id: str) -> Task | None:
        with self._connect() as conn:
            row = conn.execute(f"SELECT {_COLUMNS} FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _to_task(row) if row is not None else None

    def list(
        self,
        *,
        mission: str | None = None,
        status: TaskStatus | None = None,
        owner: str | None = None,
    ) -> TaskList:
        """Tasks, newest first, optionally filtered by mission / status / owner."""
        clauses, params = [], []
        if mission is not None:
            clauses.append("mission_id = ?")
            params.append(mission)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if owner is not None:
            clauses.append("owner = ?")
            params.append(owner)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM tasks{where} ORDER BY created_at DESC, id", params
            ).fetchall()
        return [_to_task(r) for r in rows]

    def has_dependents(self, task_id: str) -> bool:
        """True if any other task lists ``task_id`` in its ``blocked_by`` (an internal step).

        Used to decide delivery: a task feeding another is an internal step (its result stays
        on the board); only leaf tasks — the mission's deliverables — get pushed to the user.
        """
        like = f'%"{task_id}"%'  # blocked_by is a JSON array of id strings
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM tasks WHERE id != ? AND blocked_by LIKE ? LIMIT 1",
                (task_id, like),
            ).fetchone()
        return row is not None

    def children(self, task_id: str, *, open_only: bool = False) -> TaskList:
        """Tasks whose ``parent_id`` is ``task_id`` (its subtasks), newest first.

        ``open_only`` drops the finished ones (done/failed) — used by the dispatcher to
        decide whether a just-run parent must wait for subtasks it filed before completing.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE parent_id = ? ORDER BY created_at DESC", (task_id,)
            ).fetchall()
        tasks = [_to_task(row) for row in rows]
        if open_only:
            closed = {TaskStatus.DONE, TaskStatus.FAILED}
            tasks = [t for t in tasks if t.status not in closed]
        return tasks

    def ready_tasks(self) -> TaskList:
        """The dispatcher's inbox (P2): waiting tasks whose every ``blocked_by`` is done.

        Considers both ``inbox`` and ``blocked`` tasks (the dispatcher may park a task with
        unmet deps as ``blocked`` for visibility, then pick it up once they clear). A task
        with no blockers is immediately ready. Computed in Python (the dep list is a JSON
        column) — fine at board scale.
        """
        waiting = self.list(status=TaskStatus.INBOX) + self.list(status=TaskStatus.BLOCKED)
        if not waiting:
            return []
        done_ids = {t.id for t in self.list(status=TaskStatus.DONE)}
        return [t for t in waiting if all(dep in done_ids for dep in t.blocked_by)]


def _to_row(task: Task) -> tuple[Any, ...]:
    return (
        task.id,
        task.mission_id,
        task.parent_id,
        task.title,
        task.spec,
        task.status.value,
        task.assignee,
        json.dumps(task.blocked_by),
        task.depth,
        json.dumps(task.artifacts),
        task.result,
        task.notes,
        task.owner,
        task.created_by,
        task.approval_class,
        task.budget_used,
        task.notify_channel,
        task.notify_chat,
        task.created_at,
        task.updated_at,
        task.workspace,
        task.pending,
        task.pending_answer,
    )


def _to_task(row: sqlite3.Row) -> Task:
    data = dict(row)
    data["blocked_by"] = json.loads(data["blocked_by"])
    data["artifacts"] = json.loads(data["artifacts"])
    return Task.model_validate(data)
