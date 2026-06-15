"""MissionDispatcher: claim→run→complete, dependency hand-off, recovery, concurrency cap."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import gaia.missions.dispatcher as disp_mod
from gaia.missions import Task, TaskStatus, TaskStore
from gaia.missions.dispatcher import MissionDispatcher
from gaia.souls.run import SoulRun


@pytest.fixture
def store(tmp_path: Path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.db")


def _gaia(store: TaskStore) -> Any:
    # The dispatcher only touches gaia via the soul-run core (which we fake) + notify.
    return SimpleNamespace(
        connectors={},
        users=SimpleNamespace(get=lambda _id: None),
        config=SimpleNamespace(cron=SimpleNamespace(deliver=SimpleNamespace(channel="", chat=""))),
    )


def _fake_run(monkeypatch: pytest.MonkeyPatch, fn: Any) -> list[str]:
    """Make decide_soul a no-op and route execute_decision to ``fn(task_input) -> SoulRun``.

    Returns the list that captures each soul input string (for the hand-off assertion).
    """
    inputs: list[str] = []

    async def fake_decide(_gaia: Any, _task: str) -> Any:
        return SimpleNamespace(action="forge", reason="", soul_key=None, spec=None)

    async def fake_execute(
        _gaia: Any, _decision: Any, task: str, _user: str, *, state: Any = None
    ) -> SoulRun:
        inputs.append(task)
        return fn(task)

    monkeypatch.setattr(disp_mod, "decide_soul", fake_decide)
    monkeypatch.setattr(disp_mod, "execute_decision", fake_execute)
    return inputs


async def _drain(dispatcher: MissionDispatcher) -> None:
    """One poll + let spawned workers finish."""
    dispatcher._dispatch_ready()
    if dispatcher._workers:
        await asyncio.gather(*dispatcher._workers, return_exceptions=True)


async def test_ready_task_runs_and_completes(
    store: TaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_run(
        monkeypatch,
        lambda _t: SoulRun(True, "writer", "Writer", True, summary="done it", files=["out.md"]),
    )
    t = store.create(Task(title="research", owner="itay"))
    d = MissionDispatcher(_gaia(store), store=store)

    await _drain(d)

    got = store.get(t.id)
    assert got is not None
    assert got.status is TaskStatus.DONE
    assert got.result == "done it" and got.artifacts == ["out.md"] and got.assignee == "writer"


async def test_dependency_handoff_feeds_upstream_result(
    store: TaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _fake_run(
        monkeypatch,
        lambda _t: SoulRun(True, "s", "S", False, summary="T1 OUTPUT", files=["a.txt"]),
    )
    t1 = store.create(Task(title="gather", owner="itay"))
    t2 = store.create(Task(title="write up", owner="itay", blocked_by=[t1.id]))
    d = MissionDispatcher(_gaia(store), store=store)

    await _drain(d)  # T1 ready, T2 still blocked
    await _drain(d)  # T1 now done → T2 ready

    assert store.get(t1.id).status is TaskStatus.DONE  # type: ignore[union-attr]
    assert store.get(t2.id).status is TaskStatus.DONE  # type: ignore[union-attr]
    # T2's soul input carried T1's result + artifact path (the hand-off).
    t2_input = next(i for i in inputs if "write up" not in i and "T1 OUTPUT" in i)
    assert "T1 OUTPUT" in t2_input and "a.txt" in t2_input


async def test_failure_marks_failed_and_records_error(
    store: TaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_run(monkeypatch, lambda _t: SoulRun(False, "s", "S", False, error="boom"))
    t = store.create(Task(title="x", owner="itay"))
    d = MissionDispatcher(_gaia(store), store=store)

    await _drain(d)

    got = store.get(t.id)
    assert got is not None and got.status is TaskStatus.FAILED and "boom" in got.notes


async def test_only_leaf_success_is_presented(
    store: TaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # T2 depends on T1 → only T2 (the deliverable) is presented; T1 (internal step) is not.
    presented: list[str] = []

    async def spy_present(_g: Any, task: Any, _run: Any) -> None:
        presented.append(task.id)

    monkeypatch.setattr(disp_mod, "present_result", spy_present)
    _fake_run(monkeypatch, lambda _t: SoulRun(True, "s", "S", False, summary="out"))
    t1 = store.create(Task(title="step"))
    t2 = store.create(Task(title="deliverable", blocked_by=[t1.id]))
    d = MissionDispatcher(_gaia(store), store=store)

    await _drain(d)  # T1 (internal)
    await _drain(d)  # T2 (leaf)
    await asyncio.gather(*d._workers, return_exceptions=True)

    assert presented == [t2.id]  # only the leaf deliverable was presented


async def test_failure_is_notified_not_presented(
    store: TaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    presented: list[str] = []
    notified: list[str] = []

    async def spy_present(_g: Any, task: Any, _run: Any) -> None:
        presented.append(task.id)

    async def spy_notify(_g: Any, task: Any, _run: Any) -> None:
        notified.append(task.id)

    monkeypatch.setattr(disp_mod, "present_result", spy_present)
    monkeypatch.setattr(disp_mod, "notify_result", spy_notify)
    _fake_run(monkeypatch, lambda _t: SoulRun(False, "s", "S", False, error="boom"))
    t = store.create(Task(title="x"))
    d = MissionDispatcher(_gaia(store), store=store)

    await _drain(d)
    await asyncio.gather(*d._workers, return_exceptions=True)

    assert notified == [t.id] and presented == []  # failure → text notice, no present-turn


def test_recover_resets_running_to_inbox(store: TaskStore) -> None:
    t = store.create(Task(title="interrupted", owner="itay", status=TaskStatus.RUNNING))
    MissionDispatcher(_gaia(store), store=store).recover()

    assert store.get(t.id).status is TaskStatus.INBOX  # type: ignore[union-attr]


async def test_concurrency_cap_never_exceeds_limit(
    store: TaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    peak = 0
    active = 0
    gate = asyncio.Event()

    async def fake_decide(_g: Any, _t: str) -> Any:
        return SimpleNamespace()

    async def fake_execute(_g: Any, _d: Any, _t: str, _u: str) -> SoulRun:
        nonlocal peak, active
        active += 1
        peak = max(peak, active)
        await gate.wait()
        active -= 1
        return SoulRun(True, "s", "S", False, summary="ok")

    monkeypatch.setattr(disp_mod, "decide_soul", fake_decide)
    monkeypatch.setattr(disp_mod, "execute_decision", fake_execute)
    for i in range(5):
        store.create(Task(title=f"t{i}", owner="itay"))
    d = MissionDispatcher(_gaia(store), store=store, max_concurrent=2)

    d._dispatch_ready()
    await asyncio.sleep(0.05)  # let workers reach the gate
    gate.set()
    await asyncio.gather(*d._workers, return_exceptions=True)

    assert peak <= 2
