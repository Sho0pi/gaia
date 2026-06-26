"""End-to-end P3 missions (FakeLlm, no key): soul subtask re-dispatch + approval round-trip.

The integrated acceptance for #129: a soul files a subtask mid-task and yields, the
dispatcher re-runs the parent with the subtask's result; and a gated task parks for
approval, survives a simulated daemon restart, then runs once released.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from _fakes import FakeLlm
from _fakes import FakeSender as _Sender
from _fakes import call_response as _call
from _fakes import forge_response as _forge
from _fakes import reuse_response as _reuse
from _fakes import text_response as _text
from gaia import constants
from gaia.commands.base import CommandContext
from gaia.commands.task import TaskCommand
from gaia.config import Settings
from gaia.core import Gaia
from gaia.missions import Task, TaskStatus, TaskStore
from gaia.missions.dispatcher import MissionDispatcher


def _gaia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, yaml: str) -> Gaia:
    monkeypatch.setattr(constants, "AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(constants, "TASKS_DB", tmp_path / "tasks.db")
    config_path = tmp_path / "gaia.yaml"
    config_path.write_text(yaml)
    return Gaia(Settings(agent_registry_dir=tmp_path / "reg", config_path=config_path))


async def _poll_until(store: TaskStore, done: Any, *, tries: int = 500) -> None:
    # 10s budget (was 4s): the multi-hop re-dispatch chain can exceed 4s under full-suite CPU
    # contention, which flaked this test intermittently in a parallel run.
    for _ in range(tries):
        if done():
            return
        await asyncio.sleep(0.02)


async def test_soul_files_subtask_yields_and_parent_reruns_with_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia = _gaia(tmp_path, monkeypatch, "memory:\n  enabled: false\n")
    # Dispatch order: parent forged → its soul files a subtask then yields → subtask forged
    # + runs → parent re-runs and finishes. (Last response repeats for any present turn.)
    fake = FakeLlm(
        repeat_last=True,
        responses=[
            _forge("Builder"),  # smith: forge the parent's soul
            _call("task_create", title="gather facts", spec="research the topic"),  # files subtask
            _text("filed a subtask, yielding"),  # parent soul yields
            _forge("Researcher"),  # smith: forge the subtask's soul
            _text("the facts: 42"),  # subtask result
            _reuse("builder"),  # smith: parent re-run reuses its soul
            _text("done, built on: 42"),  # parent re-run final (then repeats)
        ],
    )
    monkeypatch.setattr("gaia.models.resolve_model", lambda *a, **k: fake)
    monkeypatch.setattr("gaia.agents.factory.resolve_model", lambda *a, **k: fake)
    gaia.connectors["whatsapp"] = _Sender()
    store = TaskStore()
    parent = store.create(Task(title="build", owner="itay", spec="build the thing"))

    d = MissionDispatcher(gaia, store=store, poll_seconds=0.01)
    async with gaia:
        d.start()
        await _poll_until(store, lambda: store.get(parent.id).status is TaskStatus.DONE)  # type: ignore[union-attr]
        await d.stop()

    done_parent = store.get(parent.id)
    assert done_parent is not None and done_parent.status is TaskStatus.DONE
    # A subtask was filed under the parent and ran to done; the parent's final result was
    # produced on the re-run (so it must have been re-dispatched after the subtask).
    kids = store.children(parent.id)
    assert len(kids) == 1 and kids[0].status is TaskStatus.DONE
    assert kids[0].parent_id == parent.id and kids[0].title == "gather facts"
    assert "built on" in (done_parent.result or "")


async def test_gated_task_waits_for_approval_and_survives_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia = _gaia(
        tmp_path,
        monkeypatch,
        "memory:\n  enabled: false\nmissions:\n  approval_classes: [spend]\n",
    )
    fake = FakeLlm(repeat_last=True, responses=[_forge("Booker"), _text("booked it")])
    monkeypatch.setattr("gaia.models.resolve_model", lambda *a, **k: fake)
    monkeypatch.setattr("gaia.agents.factory.resolve_model", lambda *a, **k: fake)
    gaia.connectors["whatsapp"] = _Sender()
    store = TaskStore()
    t = store.create(
        Task(title="buy flights", owner="itay", spec="book TLV-NYC", approval_class="spend")
    )

    # First daemon: the gated task parks, never runs.
    d1 = MissionDispatcher(gaia, store=store, poll_seconds=0.01)
    async with gaia:
        d1.start()
        await _poll_until(store, lambda: store.get(t.id).status is TaskStatus.AWAITING_APPROVAL)  # type: ignore[union-attr]
        await d1.stop()
    assert store.get(t.id).status is TaskStatus.AWAITING_APPROVAL  # type: ignore[union-attr]

    # Restart: a fresh dispatcher's recover() must leave the parked approval untouched.
    d2 = MissionDispatcher(gaia, store=store, poll_seconds=0.01)
    d2.recover()
    assert store.get(t.id).status is TaskStatus.AWAITING_APPROVAL  # type: ignore[union-attr]

    # Human approves via /task → it releases and runs to done.
    ctx = CommandContext(
        args=f"approve {t.id}",
        gaia=gaia,
        handler=SimpleNamespace(),  # type: ignore[arg-type]
        registry=SimpleNamespace(),  # type: ignore[arg-type]
        user_id="itay",
        session_id="s",
        role="user",
    )
    assert "Approved" in await TaskCommand().run(ctx)

    async with gaia:
        d2.start()
        await _poll_until(store, lambda: store.get(t.id).status is TaskStatus.DONE)  # type: ignore[union-attr]
        await d2.stop()
    assert store.get(t.id).status is TaskStatus.DONE  # type: ignore[union-attr]


async def test_background_soul_asks_user_then_resumes_on_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The P3 acceptance: a soul running on the dispatcher calls ask_user → the task parks at
    # AWAITING_INPUT and the question is pushed out-of-band → the user answers via /task →
    # the dispatcher resumes the soul (exact, in-process) and it finishes.
    gaia = _gaia(tmp_path, monkeypatch, "memory:\n  enabled: false\n")
    fake = FakeLlm(
        repeat_last=True,
        responses=[
            _forge("Weatherman"),  # smith forges the soul
            _call("ask_user", question="Which city?"),  # the soul asks → pauses the run
            _text("Weather for Tel Aviv: sunny"),  # after the answer, it finishes
        ],
    )
    monkeypatch.setattr("gaia.models.resolve_model", lambda *a, **k: fake)
    monkeypatch.setattr("gaia.agents.factory.resolve_model", lambda *a, **k: fake)
    sender = _Sender()
    gaia.connectors["whatsapp"] = sender
    store = TaskStore()
    t = store.create(
        Task(
            title="weather site",
            owner="itay",
            spec="build a weather page",
            notify_channel="whatsapp",
            notify_chat="972@x",
        )
    )

    d = MissionDispatcher(gaia, store=store, poll_seconds=0.01)
    async with gaia:
        d.start()
        await _poll_until(store, lambda: store.get(t.id).status is TaskStatus.AWAITING_INPUT)  # type: ignore[union-attr]
        assert any("Which city?" in m for m in sender.texts)  # asked out-of-band
        parked = store.get(t.id)
        assert parked is not None and "Which city?" in parked.pending  # parked durably

        ctx = CommandContext(
            args=f"answer {t.id} Tel Aviv",
            gaia=gaia,
            handler=SimpleNamespace(),  # type: ignore[arg-type]
            registry=SimpleNamespace(),  # type: ignore[arg-type]
            user_id="itay",
            session_id="s",
            role="user",
        )
        assert "continue" in (await TaskCommand().run(ctx)).lower()

        await _poll_until(store, lambda: store.get(t.id).status is TaskStatus.DONE)  # type: ignore[union-attr]
        await d.stop()

    done = store.get(t.id)
    assert done is not None and done.status is TaskStatus.DONE
    assert "Tel Aviv" in (done.result or "")  # resumed and finished
    assert done.pending == "" and done.pending_answer == ""  # slot cleared
