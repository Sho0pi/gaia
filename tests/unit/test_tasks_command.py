"""/tasks command: owner-scoped listing, admin sees all, approve stub."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from gaia.commands.base import CommandContext
from gaia.commands.tasks import TasksCommand
from gaia.missions import Task, TaskStatus, TaskStore


@pytest.fixture(autouse=True)
def tasks_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "tasks.db"
    monkeypatch.setattr("gaia.constants.TASKS_DB", path)  # TaskStore() default
    return path


def _ctx(*, args: str = "", user_id: str = "itay", role: str = "user") -> CommandContext:
    return CommandContext(
        args=args,
        gaia=SimpleNamespace(),  # type: ignore[arg-type]
        handler=SimpleNamespace(),  # type: ignore[arg-type]
        registry=SimpleNamespace(),  # type: ignore[arg-type]
        user_id=user_id,
        session_id="s",
        role=role,
    )


async def test_lists_only_callers_open_tasks() -> None:
    store = TaskStore()
    store.create(Task(title="mine-open", owner="itay"))
    store.create(Task(title="hers", owner="grace"))
    store.create(Task(title="mine-done", owner="itay", status=TaskStatus.DONE))

    out = await TasksCommand().run(_ctx(user_id="itay", role="user"))

    assert "mine-open" in out
    assert "hers" not in out  # other owner hidden
    assert "mine-done" not in out  # closed task hidden


async def test_admin_sees_all_owners() -> None:
    store = TaskStore()
    store.create(Task(title="itay-task", owner="itay"))
    store.create(Task(title="grace-task", owner="grace"))

    out = await TasksCommand().run(_ctx(user_id="admin", role="admin"))

    assert "itay-task" in out and "grace-task" in out


async def test_empty_board_message() -> None:
    out = await TasksCommand().run(_ctx())
    assert "No open tasks" in out


async def test_approve_is_p3_stub() -> None:
    out = await TasksCommand().run(_ctx(args="approve abc123"))
    assert "approval" in out.lower() and "abc123" in out


async def test_approve_without_id_shows_usage() -> None:
    out = await TasksCommand().run(_ctx(args="approve"))
    assert "Usage" in out
