"""Unit tests for GodHandler — the text -> ADK -> reply glue.

The ADK ``Runner`` is replaced with a fake whose ``run_async`` yields canned
events, so the streaming behaviour (one ``send`` per text part of the final
response) is verified without a model backend. ``google.genai.types`` is a real
dep (via google-adk) and constructs offline, so no key is needed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

from godpy.god.handler import GodHandler


def _event(*texts: str, final: bool = True) -> SimpleNamespace:
    """Fake ADK event carrying one Part per text (empty string -> a text-less part)."""
    parts = [SimpleNamespace(text=t or None) for t in texts]
    return SimpleNamespace(
        content=SimpleNamespace(parts=parts),
        is_final_response=lambda: final,
    )


class _FakeRunner:
    def __init__(self, events: list[SimpleNamespace]) -> None:
        self._events = events

    async def run_async(self, **_kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        for event in self._events:
            yield event


async def _collect(handler: GodHandler, text: str) -> list[str]:
    sent: list[str] = []

    async def send(reply: str) -> None:
        sent.append(reply)

    await handler(text, send)
    return sent


async def test_streams_each_text_part_of_final_response() -> None:
    # god only needs memory_service here: None short-circuits auto-ingest.
    handler = GodHandler(SimpleNamespace(memory_service=None))
    handler._runner = _FakeRunner([_event("hello", "", "world")])

    sent = await _collect(handler, "hi")

    # The empty (text-less) part is skipped; the rest stream in order.
    assert sent == ["hello", "world"]


async def test_ignores_non_final_events() -> None:
    handler = GodHandler(SimpleNamespace(memory_service=None))
    handler._runner = _FakeRunner([_event("interim", final=False), _event("done")])

    assert await _collect(handler, "hi") == ["done"]


class _ExplodingRunner:
    """A runner that fails if the model is ever invoked (commands must not reach it)."""

    async def run_async(self, **_kwargs: Any) -> Any:
        raise AssertionError("run_async must not be called for a command")
        yield  # pragma: no cover - makes this an async generator


async def test_command_runs_instead_of_model() -> None:
    from godpy.config import GodConfig

    handler = GodHandler(SimpleNamespace(config=GodConfig(), memory_service=None))
    handler._runner = _ExplodingRunner()

    sent = await _collect(handler, "/help")

    assert sent and sent[0].startswith("Commands:")  # handled out-of-band, model untouched


async def test_unknown_command_replies_hint() -> None:
    from godpy.config import GodConfig

    handler = GodHandler(SimpleNamespace(config=GodConfig(), memory_service=None))
    handler._runner = _ExplodingRunner()

    assert await _collect(handler, "/nope") == ["Unknown command '/nope'. Try /help."]


async def test_plain_text_still_reaches_the_model() -> None:
    handler = GodHandler(SimpleNamespace(memory_service=None))
    handler._runner = _FakeRunner([_event("answer")])

    assert await _collect(handler, "not a command") == ["answer"]


def _god(*, batch_size: int = 2, interval: int = 3600, auto_ingest: bool = True) -> Any:
    """Fake God whose memory service records each add_events_to_memory call."""
    calls: list[dict[str, Any]] = []

    async def add_events_to_memory(**kwargs: Any) -> None:
        calls.append(kwargs)

    service = SimpleNamespace(calls=calls, add_events_to_memory=add_events_to_memory)
    memory = SimpleNamespace(
        auto_ingest=auto_ingest,
        ingest_batch_size=batch_size,
        ingest_interval_seconds=interval,
    )
    return SimpleNamespace(memory_service=service, config=SimpleNamespace(memory=memory))


async def test_buffers_until_batch_size_then_flushes_once() -> None:
    god = _god(batch_size=2)
    handler = GodHandler(god)
    handler._runner = _FakeRunner([_event("ok")])  # one event per turn

    await _collect(handler, "msg 1")
    assert god.memory_service.calls == []  # 1 < 2 buffered, nothing ingested yet

    await _collect(handler, "msg 2")
    assert len(god.memory_service.calls) == 1  # threshold reached → single flush
    assert len(god.memory_service.calls[0]["events"]) == 2  # both turns in one batch
    assert handler._buffer == []  # buffer drained


async def test_flush_drains_remaining_buffer() -> None:
    god = _god(batch_size=100)  # never auto-flushes
    handler = GodHandler(god)
    handler._runner = _FakeRunner([_event("ok")])

    await _collect(handler, "lonely message")
    assert god.memory_service.calls == []  # below threshold

    await handler.flush()  # shutdown-style drain
    assert len(god.memory_service.calls) == 1
    assert len(god.memory_service.calls[0]["events"]) == 1
    assert handler._buffer == []


async def test_auto_ingest_off_never_buffers() -> None:
    god = _god(auto_ingest=False)
    handler = GodHandler(god)
    handler._runner = _FakeRunner([_event("ok")])

    await _collect(handler, "msg")

    assert handler._buffer == []
    await handler.flush()
    assert god.memory_service.calls == []
