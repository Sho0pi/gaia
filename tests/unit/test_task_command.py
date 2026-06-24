"""/tasks command: owner-scoped listing, admin sees all, approve stub."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from gaia.commands.base import CommandContext
from gaia.commands.task import TaskCommand
from gaia.missions import Task, TaskStatus, TaskStore


@pytest.fixture(autouse=True)
def tasks_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "tasks.db"
    monkeypatch.setattr("gaia.constants.TASKS_DB", path)  # TaskStore() default
    return path


def _fake_gaia() -> SimpleNamespace:
    # The command reads gaia.tasks; reject also pushes (best-effort) via notify → users/
    # connectors/config. No connector running + no address → notify no-ops cleanly.
    return SimpleNamespace(
        tasks=TaskStore(),
        users=SimpleNamespace(get=lambda _id: None),
        connectors={},
        config=SimpleNamespace(cron=SimpleNamespace(deliver=SimpleNamespace(channel="", chat=""))),
    )


def _ctx(*, args: str = "", user_id: str = "itay", role: str = "user") -> CommandContext:
    return CommandContext(
        args=args,
        gaia=_fake_gaia(),  # type: ignore[arg-type]
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

    out = await TaskCommand().run(_ctx(user_id="itay", role="user"))

    assert "mine-open" in out
    assert "hers" not in out  # other owner hidden
    assert "mine-done" not in out  # closed task hidden


async def test_admin_sees_all_owners() -> None:
    store = TaskStore()
    store.create(Task(title="itay-task", owner="itay"))
    store.create(Task(title="grace-task", owner="grace"))

    out = await TaskCommand().run(_ctx(user_id="admin", role="admin"))

    assert "itay-task" in out and "grace-task" in out


async def test_empty_board_message() -> None:
    out = await TaskCommand().run(_ctx())
    assert "No open tasks" in out


async def test_approve_releases_awaiting_task() -> None:
    ctx = _ctx(args="", user_id="itay")
    store = ctx.gaia.tasks  # type: ignore[attr-defined]
    t = store.create(Task(title="buy", owner="itay", status=TaskStatus.AWAITING_APPROVAL))

    out = await TaskCommand().run(_ctx(args=f"approve {t.id}", user_id="itay"))

    assert "Approved" in out
    assert store.get(t.id).status is TaskStatus.INBOX  # type: ignore[union-attr]


async def test_reject_fails_awaiting_task() -> None:
    ctx = _ctx()
    store = ctx.gaia.tasks  # type: ignore[attr-defined]
    t = store.create(Task(title="buy", owner="itay", status=TaskStatus.AWAITING_APPROVAL))

    out = await TaskCommand().run(_ctx(args=f"reject {t.id}", user_id="itay"))

    assert "Rejected" in out
    assert store.get(t.id).status is TaskStatus.FAILED  # type: ignore[union-attr]


async def test_approve_non_awaiting_task_is_refused() -> None:
    ctx = _ctx()
    store = ctx.gaia.tasks  # type: ignore[attr-defined]
    t = store.create(Task(title="x", owner="itay", status=TaskStatus.INBOX))

    out = await TaskCommand().run(_ctx(args=f"approve {t.id}", user_id="itay"))

    assert "isn't awaiting approval" in out


async def test_approve_other_owners_task_hidden() -> None:
    ctx = _ctx()
    store = ctx.gaia.tasks  # type: ignore[attr-defined]
    t = store.create(Task(title="hers", owner="grace", status=TaskStatus.AWAITING_APPROVAL))

    out = await TaskCommand().run(_ctx(args=f"approve {t.id}", user_id="itay"))

    assert "No task" in out  # not yours → treated as absent


async def test_approve_without_id_shows_usage() -> None:
    out = await TaskCommand().run(_ctx(args="approve"))
    assert "Usage" in out


async def test_answer_records_answer_and_releases_to_inbox() -> None:
    ctx = _ctx(user_id="itay")
    store = ctx.gaia.tasks  # type: ignore[attr-defined]
    t = store.create(
        Task(title="weather", owner="itay", status=TaskStatus.AWAITING_INPUT, pending="{}")
    )

    out = await TaskCommand().run(_ctx(args=f"answer {t.id} Tel Aviv", user_id="itay"))

    assert "continue" in out.lower()
    got = store.get(t.id)
    assert got is not None and got.status is TaskStatus.INBOX and got.pending_answer == "Tel Aviv"


async def test_answer_non_awaiting_task_is_refused() -> None:
    ctx = _ctx()
    store = ctx.gaia.tasks  # type: ignore[attr-defined]
    t = store.create(Task(title="x", owner="itay", status=TaskStatus.INBOX))

    out = await TaskCommand().run(_ctx(args=f"answer {t.id} hi", user_id="itay"))

    assert "isn't waiting" in out
    assert store.get(t.id).pending_answer == ""  # type: ignore[union-attr]


async def test_answer_not_your_task_is_refused() -> None:
    ctx = _ctx()
    store = ctx.gaia.tasks  # type: ignore[attr-defined]
    t = store.create(Task(title="x", owner="grace", status=TaskStatus.AWAITING_INPUT))

    out = await TaskCommand().run(_ctx(args=f"answer {t.id} hi", user_id="itay", role="user"))

    assert "No task" in out  # not the caller's task
