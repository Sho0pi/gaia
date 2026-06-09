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


def _ask_event(call_id: str, name: str, args: dict[str, Any]) -> SimpleNamespace:
    """Fake ADK event surfacing a long-running function call (an ``ask``)."""
    call = SimpleNamespace(id=call_id, name=name, args=args)
    return SimpleNamespace(
        content=SimpleNamespace(parts=[]),
        long_running_tool_ids={call_id},
        get_function_calls=lambda: [call],
        is_final_response=lambda: False,
    )


class _FakeRunner:
    def __init__(self, events: list[SimpleNamespace]) -> None:
        self._events = events
        self.messages: list[Any] = []

    async def run_async(self, *, new_message: Any = None, **_kwargs: Any) -> AsyncIterator[Any]:
        self.messages.append(new_message)
        for event in self._events:
            yield event


async def _collect(handler: GodHandler, text: str) -> list[str]:
    sent: list[str] = []

    async def send(reply: str) -> None:
        sent.append(reply)

    await handler(text, send)
    return sent


async def test_streams_each_text_part_of_final_response() -> None:
    handler = GodHandler(SimpleNamespace())  # god unused: runner is pre-set below
    handler._runner = _FakeRunner([_event("hello", "", "world")])

    sent = await _collect(handler, "hi")

    # The empty (text-less) part is skipped; the rest stream in order.
    assert sent == ["hello", "world"]


async def test_ignores_non_final_events() -> None:
    handler = GodHandler(SimpleNamespace())
    handler._runner = _FakeRunner([_event("interim", final=False), _event("done")])

    assert await _collect(handler, "hi") == ["done"]


async def test_ask_pauses_and_renders_question_via_text_floor() -> None:
    handler = GodHandler(SimpleNamespace())
    args = {"question": "Which env?", "options": ["dev", "prod"]}
    # A trailing text event must NOT be streamed once the ask short-circuits.
    handler._runner = _FakeRunner([_ask_event("c1", "ask", args), _event("ignored")])

    sent = await _collect(handler, "deploy it")

    assert len(sent) == 1
    assert "Which env?" in sent[0] and "1. dev" in sent[0]
    assert handler._pending == {"call_id": "c1", "name": "ask"}


async def test_ask_uses_native_send_ask_when_present() -> None:
    handler = GodHandler(SimpleNamespace())
    handler._runner = _FakeRunner([_ask_event("c1", "ask", {"question": "Q", "options": ["a"]})])
    asks: list[Any] = []

    async def send(reply: str) -> None:  # pragma: no cover - should not be called
        raise AssertionError("native asker should be used, not text send")

    async def ask(question: Any) -> None:
        asks.append(question)

    send.ask = ask  # type: ignore[attr-defined]
    await handler("go", send)

    assert len(asks) == 1
    assert asks[0].question == "Q" and asks[0].options == ["a"]


async def test_next_message_resumes_with_function_response() -> None:
    from google.genai import types

    handler = GodHandler(SimpleNamespace())
    handler._runner = _FakeRunner([_ask_event("c1", "ask", {"question": "Which env?"})])
    await _collect(handler, "deploy it")
    assert handler._pending is not None

    # The answer turn: a fresh runner yields the final reply; the message it receives
    # must be a FunctionResponse matched to the dangling call, not a user-text turn.
    runner = _FakeRunner([_event("done")])
    handler._runner = runner
    sent = await _collect(handler, "prod")

    assert sent == ["done"]
    assert handler._pending is None
    resume = runner.messages[0]
    fr = resume.parts[0].function_response
    assert isinstance(fr, types.FunctionResponse)
    assert fr.id == "c1" and fr.name == "ask"
    assert fr.response == {"status": "success", "answer": "prod"}


async def test_non_ask_long_running_id_is_ignored() -> None:
    handler = GodHandler(SimpleNamespace())
    handler._runner = _FakeRunner(
        [_ask_event("c9", "some_other_tool", {}), _event("normal answer")]
    )

    sent = await _collect(handler, "hi")

    assert sent == ["normal answer"]
    assert handler._pending is None
