"""End-to-end P3 missions (FakeLlm, no key): soul subtask re-dispatch + approval round-trip.

The integrated acceptance for #129: a soul files a subtask mid-task and yields, the
dispatcher re-runs the parent with the subtask's result; and a gated task parks for
approval, survives a simulated daemon restart, then runs once released.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from gaia import constants
from gaia.agents import AgentSpec
from gaia.commands.base import CommandContext
from gaia.commands.tasks import TasksCommand
from gaia.config import Settings
from gaia.core import Gaia
from gaia.missions import Task, TaskStatus, TaskStore
from gaia.missions.dispatcher import MissionDispatcher
from gaia.souls.smith import SoulDecision


class FakeLlm(BaseLlm):
    """Pops scripted responses in order; repeats the last one if the script runs out.

    Repeating keeps best-effort follow-ups (e.g. the leaf-present Gaia turn) from
    crashing the test on an exhausted list — we assert on board state, not call count.
    """

    model: str = "fake-model"
    responses: list[LlmResponse]

    async def generate_content_async(
        self, llm_request: Any, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        yield self.responses[0] if len(self.responses) == 1 else self.responses.pop(0)


def _text(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


def _call(name: str, **args: Any) -> LlmResponse:
    part = types.Part(function_call=types.FunctionCall(name=name, args=args))
    return LlmResponse(content=types.Content(role="model", parts=[part]))


def _forge(name: str) -> LlmResponse:
    return _text(
        SoulDecision(
            action="forge",
            reason=f"need a {name}",
            spec=AgentSpec(name=name, description=f"a {name}", instruction="Do it.", model="fake"),
        ).model_dump_json()
    )


def _reuse(key: str) -> LlmResponse:
    return _text(SoulDecision(action="reuse", reason="fits", soul_key=key).model_dump_json())


class _Sender:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_to(self, chat: str, reply: Any) -> None:
        self.sent.append(reply if isinstance(reply, str) else f"[media {reply.path}]")


def _gaia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, yaml: str) -> Gaia:
    monkeypatch.setattr(constants, "AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(constants, "TASKS_DB", tmp_path / "tasks.db")
    config_path = tmp_path / "gaia.yaml"
    config_path.write_text(yaml)
    return Gaia(Settings(agent_registry_dir=tmp_path / "reg", config_path=config_path))


async def _poll_until(store: TaskStore, done: Any, *, tries: int = 200) -> None:
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
        responses=[
            _forge("Builder"),  # smith: forge the parent's soul
            _call("task_create", title="gather facts", spec="research the topic"),  # files subtask
            _text("filed a subtask, yielding"),  # parent soul yields
            _forge("Researcher"),  # smith: forge the subtask's soul
            _text("the facts: 42"),  # subtask result
            _reuse("builder"),  # smith: parent re-run reuses its soul
            _text("done, built on: 42"),  # parent re-run final (then repeats)
        ]
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
    fake = FakeLlm(responses=[_forge("Booker"), _text("booked it")])
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

    # Human approves via /tasks → it releases and runs to done.
    ctx = CommandContext(
        args=f"approve {t.id}",
        gaia=gaia,
        handler=SimpleNamespace(),  # type: ignore[arg-type]
        registry=SimpleNamespace(),  # type: ignore[arg-type]
        user_id="itay",
        session_id="s",
        role="user",
    )
    assert "Approved" in await TasksCommand().run(ctx)

    async with gaia:
        d2.start()
        await _poll_until(store, lambda: store.get(t.id).status is TaskStatus.DONE)  # type: ignore[union-attr]
        await d2.stop()
    assert store.get(t.id).status is TaskStatus.DONE  # type: ignore[union-attr]
