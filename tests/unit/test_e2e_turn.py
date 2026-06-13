"""Offline end-to-end turn: message → Runner → root agent → real tool → final reply.

The one place the whole ADK pipeline runs without a model key (issue #59). A scripted
:class:`FakeLlm` (ADK's supported ``BaseLlm`` seam) stands in for Gemini; everything
else is real — ``Gaia``, ``build_handler``'s ``Runner`` + ``InMemorySessionService``,
the tool schemas ADK derives from our closures, the sandboxed ``fs_write`` execution,
and ``ToolLoggingPlugin``'s ``tool_used`` event. The handler tests elsewhere fake the
Runner itself; this is the wiring those fakes skip.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from gaia import constants
from gaia.config import Settings
from gaia.core import Gaia
from gaia.core.handler import build_handler


class FakeLlm(BaseLlm):
    """Scripted model: yields one canned LlmResponse per generate call, in order."""

    model: str = "fake-model"
    responses: list[LlmResponse]
    calls: int = 0

    async def generate_content_async(
        self, llm_request: Any, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.calls += 1
        yield self.responses.pop(0)


def _text(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


def _tool_call(name: str, **args: Any) -> LlmResponse:
    part = types.Part(function_call=types.FunctionCall(name=name, args=args))
    return LlmResponse(content=types.Content(role="model", parts=[part]))


@pytest.fixture
def gaia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Gaia:
    """A real Gaia on a tmp home: memory off, tmp agents dir, no souls."""
    monkeypatch.setattr(constants, "AGENTS_DIR", tmp_path / "agents")  # fs tools bind at build
    config_path = tmp_path / "gaia.yaml"
    config_path.write_text("memory:\n  enabled: false\n")
    return Gaia(Settings(agent_registry_dir=tmp_path / "reg", config_path=config_path))


def _install(monkeypatch: pytest.MonkeyPatch, fake: FakeLlm) -> None:
    """Route the root agent's model resolution to the scripted fake."""
    monkeypatch.setattr("gaia.core.agent.resolve_model", lambda *a, **k: fake)


async def _run_turn(gaia: Gaia, text: str) -> list[str]:
    replies: list[str] = []

    async def send(reply: Any) -> None:
        replies.append(str(reply))

    await build_handler(gaia)(text, send)
    return replies


async def test_full_turn_with_real_tool_execution(
    gaia: Gaia, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeLlm(
        responses=[
            _tool_call("fs_write", path="hello.txt", content="hi from the model"),
            _text("done — wrote hello.txt"),
        ]
    )
    _install(monkeypatch, fake)

    with caplog.at_level(logging.INFO, logger=constants.EVENTS_LOGGER_NAME):
        replies = await _run_turn(gaia, "please write hello.txt")

    # The final reply made it back through the Runner to the connector seam.
    assert replies == ["done — wrote hello.txt"]
    # The tool REALLY executed: schema -> closure -> sandbox -> disk.
    written = tmp_path / "agents" / "gaia" / "workspace" / "hello.txt"
    assert written.read_text() == "hi from the model"
    # The function-response round-trip happened (call 1: tool, call 2: final text).
    assert fake.calls == 2
    # ToolLoggingPlugin emitted exactly one tool_used event for the call.
    tool_events = [r for r in caplog.records if r.getMessage() == "tool_used"]
    assert len(tool_events) == 1
    assert tool_events[0].tool == "fs_write"  # type: ignore[attr-defined]
    assert tool_events[0].status == "success"  # type: ignore[attr-defined]


async def test_text_only_turn_runs_no_tool(
    gaia: Gaia, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeLlm(responses=[_text("just an answer")])
    _install(monkeypatch, fake)

    with caplog.at_level(logging.INFO, logger=constants.EVENTS_LOGGER_NAME):
        replies = await _run_turn(gaia, "hi")

    assert replies == ["just an answer"]
    assert fake.calls == 1
    assert not [r for r in caplog.records if r.getMessage() == "tool_used"]


async def test_two_users_get_separate_memory_partitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The multi-user payoff: two senders dispatched through the same process land in
    # distinct mem0 partitions (keyed by their canonical user_id), with no key leakage.
    from gaia.core.dispatch import Dispatcher

    monkeypatch.setattr(constants, "AGENTS_DIR", tmp_path / "agents")
    config_path = tmp_path / "gaia.yaml"
    # memory on + flush every turn so each turn's add is observable
    config_path.write_text(
        "memory:\n  enabled: true\n  auto_ingest: true\n  ingest_batch_size: 1\n"
    )
    gaia = Gaia(Settings(agent_registry_dir=tmp_path / "reg", config_path=config_path))
    gaia.users.register("whatsapp", "111@s.whatsapp.net", "Itay", role="admin")
    gaia.users.register("whatsapp", "972@s.whatsapp.net", "Grace", role="user")

    from google.adk.memory.base_memory_service import BaseMemoryService, SearchMemoryResponse

    partitions: list[str] = []

    class _RecordingMemory(BaseMemoryService):  # ADK validates the Runner's memory_service type
        async def add_session_to_memory(self, session: Any) -> None:  # pragma: no cover
            return None

        async def search_memory(self, *, app_name: str, user_id: str, query: str) -> Any:
            return SearchMemoryResponse(memories=[])

        async def add_events_to_memory(self, *, app_name: str, user_id: str, **_kw: Any) -> None:
            partitions.append(user_id)

    recorder = _RecordingMemory()
    monkeypatch.setattr(type(gaia), "memory_service", property(lambda _self: recorder))
    _install(monkeypatch, FakeLlm(responses=[_text("hi Itay"), _text("hi Grace")]))

    async def send(_reply: Any) -> None:
        return None

    wa = Dispatcher(gaia).for_channel("whatsapp")
    await wa("111@s.whatsapp.net", "Itay", "remember me", send)
    await wa("972@s.whatsapp.net", "Grace", "remember me", send)

    assert set(partitions) == {"itay", "grace"}  # two people, two memory partitions


async def test_tool_error_turn_still_completes(
    gaia: Gaia, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # The tool refuses (sandbox escape) but never raises: the error dict goes back to
    # the model, which still produces a final reply — the turn must not blow up.
    fake = FakeLlm(
        responses=[
            _tool_call("fs_write", path="/etc/evil.txt", content="nope"),
            _text("that path is not allowed"),
        ]
    )
    _install(monkeypatch, fake)

    with caplog.at_level(logging.INFO, logger=constants.EVENTS_LOGGER_NAME):
        replies = await _run_turn(gaia, "write outside the sandbox")

    assert replies == ["that path is not allowed"]
    tool_events = [r for r in caplog.records if r.getMessage() == "tool_used"]
    assert len(tool_events) == 1
    assert tool_events[0].status == "error"  # type: ignore[attr-defined]
    assert not os.path.exists("/etc/evil.txt")  # noqa: ASYNC240 - one sync stat in a test
