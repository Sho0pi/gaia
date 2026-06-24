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


def _gaia(
    store: TaskStore,
    *,
    approval_classes: list[str] | None = None,
    has_session: bool = True,
    pinned: list[str] | None = None,
    unpinned: list[str] | None = None,
) -> Any:
    # The dispatcher only touches gaia via the soul-run core (which we fake) + notify +
    # soul_sessions (pin/unpin/has for the P3 ask_user pause).
    pins = pinned if pinned is not None else []
    unpins = unpinned if unpinned is not None else []
    return SimpleNamespace(
        connectors={},
        users=SimpleNamespace(get=lambda _id: None),
        config=SimpleNamespace(
            cron=SimpleNamespace(deliver=SimpleNamespace(channel="", chat="")),
            missions=SimpleNamespace(approval_classes=approval_classes or [], max_hours=0.0),
        ),
        soul_sessions=SimpleNamespace(
            pin=lambda k: pins.append(k),
            unpin=lambda k: unpins.append(k),
            has=lambda _k: has_session,
        ),
    )


def _pending_run(question: str, *, soul_key: str = "writer", project: str = "p") -> SoulRun:
    """A SoulRun whose soul paused on ask_user (P3) — what execute_decision returns mid-run."""
    from gaia.core.elicit import SoulPending

    pending = SoulPending(
        warm_key=f"{soul_key}/{project}",
        soul_key=soul_key,
        project=project,
        soul_fc_id="sfc",
        question=question,
        user_id="itay",
    )
    return SoulRun(False, soul_key, "S", False, pending=pending)


def _fake_run(monkeypatch: pytest.MonkeyPatch, fn: Any) -> list[str]:
    """Make decide_soul a no-op and route execute_decision to ``fn(task_input) -> SoulRun``.

    Returns the list that captures each soul input string (for the hand-off assertion).
    """
    inputs: list[str] = []

    async def fake_decide(_gaia: Any, _task: str) -> Any:
        return SimpleNamespace(action="forge", reason="", soul_key=None, spec=None)

    async def fake_execute(
        _gaia: Any,
        _decision: Any,
        task: str,
        _user: str,
        *,
        project: str = "",
        attachments: Any = None,
        state: Any = None,
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


async def test_dependency_handoff_copies_upstream_files(
    store: TaskStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The async twin of a delegation attachment: a dependency's files are handed to the
    # dependent as attachments (absolute paths from the upstream's workspace + artifacts), so
    # the downstream soul opens them as relative files — not a path string it can't read.
    ws = str(tmp_path / "upstream_ws")
    captured: dict[str, Any] = {}

    async def fake_decide(_g: Any, _t: str) -> Any:
        return SimpleNamespace(action="forge", reason="", soul_key=None, spec=None)

    async def fake_execute(
        _g: Any,
        _d: Any,
        task: str,
        _u: str,
        *,
        project: str = "",
        attachments: Any = None,
        state: Any = None,
    ) -> SoulRun:
        if attachments:  # the dependent (T2) — it received the upstream's files
            captured["attachments"] = attachments
            return SoulRun(True, "s", "S", False, summary="built")
        return SoulRun(True, "s", "S", False, summary="T1 OUTPUT", files=["a.txt"], workspace=ws)

    monkeypatch.setattr(disp_mod, "decide_soul", fake_decide)
    monkeypatch.setattr(disp_mod, "execute_decision", fake_execute)

    t1 = store.create(Task(title="gather", owner="itay"))
    t2 = store.create(Task(title="write up", owner="itay", blocked_by=[t1.id]))
    d = MissionDispatcher(_gaia(store), store=store)

    await _drain(d)  # T1 runs → records workspace + artifacts
    await _drain(d)  # T2 ready → receives T1's file as an attachment

    assert store.get(t1.id).workspace == ws  # type: ignore[union-attr]
    assert captured["attachments"] == [str(Path(ws) / "a.txt")]
    assert store.get(t2.id).status is TaskStatus.DONE  # type: ignore[union-attr]


async def test_parent_blocks_on_filed_subtask_then_reruns_with_results(
    store: TaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A soul running the parent files a subtask + yields → parent must wait (BLOCKED), not
    # complete; once the subtask is done the parent is re-run with its notes + the result.
    parent = store.create(Task(title="build", owner="itay", spec="build the thing"))
    calls = {"n": 0}

    def run_fn(task_input: str) -> SoulRun:
        calls["n"] += 1
        if calls["n"] == 1:  # parent's first run: file a subtask and yield
            store.create(
                Task(title="sub", owner="itay", parent_id=parent.id, mission_id=parent.mission_id)
            )
            return SoulRun(True, "builder", "Builder", True, summary="need a subtask first")
        return SoulRun(True, calls.get("soul", "x"), "S", False, summary="ran", files=["o.txt"])

    inputs = _fake_run(monkeypatch, run_fn)
    d = MissionDispatcher(_gaia(store), store=store)

    await _drain(d)  # parent runs → files child → parent BLOCKED on it
    blocked = store.get(parent.id)
    assert blocked is not None and blocked.status is TaskStatus.BLOCKED
    child = store.children(parent.id)[0]
    assert child.id in blocked.blocked_by and blocked.result == ""  # not completed
    assert "need a subtask first" in blocked.notes  # saved as the re-run input

    await _drain(d)  # child (inbox) runs → done
    await _drain(d)  # parent re-dispatched with notes + child result

    done = store.get(parent.id)
    assert done is not None and done.status is TaskStatus.DONE
    rerun = inputs[-1]  # the parent's re-run prompt
    assert "need a subtask first" in rerun and "ran" in rerun  # notes + subtask result fed back


async def test_gated_task_parks_for_approval_not_run(
    store: TaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A task in a configured approval class must park (awaiting_approval), never dispatch.
    ran: list[str] = []
    _fake_run(monkeypatch, lambda t: ran.append(t) or SoulRun(True, "s", "S", False, summary="x"))  # type: ignore[func-returns-value]
    t = store.create(Task(title="buy flights", owner="itay", approval_class="spend"))
    d = MissionDispatcher(_gaia(store, approval_classes=["spend"]), store=store)

    await _drain(d)

    assert store.get(t.id).status is TaskStatus.AWAITING_APPROVAL  # type: ignore[union-attr]
    assert ran == []  # never executed
    # Approval consumes the gate (clears approval_class) → inbox → next poll runs it.
    t.approval_class = ""
    t.status = TaskStatus.INBOX
    store.update(t)
    await _drain(d)
    assert store.get(t.id).status is TaskStatus.DONE  # type: ignore[union-attr]


async def test_ungated_class_runs_normally(
    store: TaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_run(monkeypatch, lambda _t: SoulRun(True, "s", "S", False, summary="ok"))
    t = store.create(Task(title="x", owner="itay", approval_class="spend"))
    d = MissionDispatcher(_gaia(store, approval_classes=[]), store=store)  # spend not gated

    await _drain(d)

    assert store.get(t.id).status is TaskStatus.DONE  # type: ignore[union-attr]


async def test_mission_over_time_budget_pauses(
    store: TaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    # max_hours breached → the mission root parks (awaiting_approval), the task doesn't run.
    ran: list[str] = []
    _fake_run(monkeypatch, lambda t: ran.append(t) or SoulRun(True, "s", "S", False))  # type: ignore[func-returns-value]
    root = store.create(Task(title="old mission", owner="itay"))
    root.created_at = "2000-01-01T00:00:00"  # ancient → way over any budget
    store.update(root)
    gaia = _gaia(store)
    gaia.config.missions.max_hours = 1.0
    d = MissionDispatcher(gaia, store=store)

    await _drain(d)

    assert store.get(root.id).status is TaskStatus.AWAITING_APPROVAL  # type: ignore[union-attr]
    assert ran == []  # over budget → never ran


def test_recover_leaves_awaiting_approval_untouched(store: TaskStore) -> None:
    # Restart safety: recover() only resets RUNNING; a parked approval survives a reboot.
    parked = store.create(Task(title="buy", owner="itay", status=TaskStatus.AWAITING_APPROVAL))
    running = store.create(Task(title="mid", owner="itay", status=TaskStatus.RUNNING))
    d = MissionDispatcher(_gaia(store), store=store)

    d.recover()

    assert store.get(parked.id).status is TaskStatus.AWAITING_APPROVAL  # type: ignore[union-attr]
    assert store.get(running.id).status is TaskStatus.INBOX  # type: ignore[union-attr]


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


async def _spy_ask(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Capture notify_ask_user(task, question) calls (no real connector in the fake gaia)."""
    asked: list[tuple[str, str]] = []

    async def spy(_g: Any, task: Any, question: str, options: Any = ()) -> None:
        asked.append((task.id, question))

    monkeypatch.setattr(disp_mod, "notify_ask_user", spy)
    return asked


async def test_soul_ask_user_parks_task_and_asks_out_of_band(
    store: TaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = await _spy_ask(monkeypatch)
    _fake_run(monkeypatch, lambda _t: _pending_run("Which city?"))
    pins: list[str] = []
    t = store.create(Task(title="weather site", owner="itay"))
    d = MissionDispatcher(_gaia(store, pinned=pins), store=store)

    await _drain(d)

    got = store.get(t.id)
    assert got is not None and got.status is TaskStatus.AWAITING_INPUT
    assert "Which city?" in got.pending and got.pending_answer == ""  # parked durably
    assert pins == ["writer/p"]  # warm session pinned against the reaper
    assert asked == [(t.id, "Which city?")]  # pushed out-of-band


async def test_answer_resumes_exact_run_when_session_is_live(
    store: TaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _spy_ask(monkeypatch)
    _fake_run(monkeypatch, lambda _t: _pending_run("city?"))
    unpins: list[str] = []
    gaia = _gaia(store, has_session=True, unpinned=unpins)
    t = store.create(Task(title="x", owner="itay"))
    d = MissionDispatcher(gaia, store=store)
    await _drain(d)  # parked at AWAITING_INPUT

    resumed: list[tuple[str, str]] = []

    async def fake_resume(_g: Any, pending: Any, answer: str) -> SoulRun:
        resumed.append((pending.warm_key, answer))
        return SoulRun(True, "writer", "Writer", False, summary="weather for Tel Aviv")

    monkeypatch.setattr(disp_mod, "resume_soul", fake_resume)
    parked = store.get(t.id)
    assert parked is not None
    parked.pending_answer, parked.status = "Tel Aviv", TaskStatus.INBOX  # /task answer did this
    store.update(parked)

    await _drain(d)

    got = store.get(t.id)
    assert (
        got is not None and got.status is TaskStatus.DONE and got.result == "weather for Tel Aviv"
    )
    assert got.pending == "" and got.pending_answer == ""  # slot cleared
    assert resumed == [("writer/p", "Tel Aviv")]  # exact resume with the answer
    assert unpins == ["writer/p"]  # session released


async def test_answer_reruns_cold_after_restart(
    store: TaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _spy_ask(monkeypatch)
    calls: list[dict[str, Any]] = []

    async def fake_decide(_g: Any, _t: str) -> Any:
        return SimpleNamespace(action="forge", reason="", soul_key=None, spec=None)

    async def fake_execute(
        _g: Any,
        decision: Any,
        task: str,
        _u: str,
        *,
        project: str = "",
        attachments: Any = None,
        state: Any = None,
    ) -> SoulRun:
        calls.append({"task": task, "project": project, "decision": decision})
        if "They answered" in task:  # the cold re-run with the Q&A folded in
            return SoulRun(True, "writer", "Writer", False, summary="done cold")
        return _pending_run("city?")

    monkeypatch.setattr(disp_mod, "decide_soul", fake_decide)
    monkeypatch.setattr(disp_mod, "execute_decision", fake_execute)
    t = store.create(Task(title="x", owner="itay", spec="build weather site"))
    d = MissionDispatcher(_gaia(store, has_session=False), store=store)  # session gone (restarted)
    await _drain(d)  # parked

    parked = store.get(t.id)
    assert parked is not None
    parked.pending_answer, parked.status = "Tel Aviv", TaskStatus.INBOX
    store.update(parked)

    await _drain(d)  # cold resume → re-run the same soul in its workspace

    got = store.get(t.id)
    assert got is not None and got.status is TaskStatus.DONE and got.result == "done cold"
    rerun = calls[-1]
    assert rerun["project"] == "p"  # reused the paused soul's workspace project
    assert rerun["decision"].action == "reuse" and rerun["decision"].soul_key == "writer"
    assert "They answered: Tel Aviv" in rerun["task"]  # the Q&A folded into the prompt


async def test_resume_can_re_park_on_a_second_question(
    store: TaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _spy_ask(monkeypatch)
    _fake_run(monkeypatch, lambda _t: _pending_run("first?"))
    t = store.create(Task(title="x", owner="itay"))
    d = MissionDispatcher(_gaia(store, has_session=True), store=store)
    await _drain(d)  # parked on first?

    parked = store.get(t.id)
    assert parked is not None
    parked.pending_answer, parked.status = "a1", TaskStatus.INBOX
    store.update(parked)

    async def fake_resume(_g: Any, _p: Any, _a: str) -> SoulRun:
        return _pending_run("second?")  # the soul asks a follow-up

    monkeypatch.setattr(disp_mod, "resume_soul", fake_resume)
    await _drain(d)

    got = store.get(t.id)
    assert got is not None and got.status is TaskStatus.AWAITING_INPUT
    assert "second?" in got.pending and got.pending_answer == ""  # re-parked, old answer consumed
