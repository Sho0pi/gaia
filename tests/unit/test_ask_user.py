"""Unit tests for the ask_user human-in-the-loop elicitation feature.

Covers three layers without a model backend: the tool (pauses by returning None +
flipping off summarization), the pure resolve/render logic, and the handler's
pause→surface→resume wiring (driven by a fake Runner that yields a long-running
ask_user call, then canned continuation events).
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

from gaia.connectors.base import Inbound, Question, as_text
from gaia.core.elicit import Pending, resolve_answer
from gaia.core.handler import GaiaHandler
from gaia.tools.ask_user import make_ask_user


def _poll(*labels: str) -> str:
    """The "[poll:…]" text a WhatsApp poll vote for ``labels`` decodes to."""
    return "[poll:" + ",".join(hashlib.sha256(label.encode()).hexdigest() for label in labels) + "]"


# --- the tool: returns None to pause, turns off result summarization ----------------


def test_ask_user_pauses_and_skips_summarization() -> None:
    tool = make_ask_user()
    assert tool.is_long_running is True  # ADK marks the call so run_async completes (pauses)

    ctx = SimpleNamespace(actions=SimpleNamespace(skip_summarization=False))
    result = tool.func(question="pick one", options=["a", "b"], multi=True, tool_context=ctx)

    assert result is None  # a falsy return is what makes ADK emit no function-response
    assert ctx.actions.skip_summarization is True  # model continues straight from the answer


# --- resolve_answer: map a raw reply back to the chosen option ----------------------


def test_resolve_answer_by_number() -> None:
    pending = Pending(fc_id="x", options=("apple", "banana", "cherry"))
    assert resolve_answer(pending, " 2 ") == "banana"


def test_resolve_answer_by_selected_label() -> None:
    pending = Pending(fc_id="x", options=("Yes", "No"))
    assert resolve_answer(pending, "[Selected: no]") == "No"  # WhatsApp tap, case-insensitive


def test_resolve_answer_out_of_range_falls_through() -> None:
    pending = Pending(fc_id="x", options=("a", "b"))
    assert resolve_answer(pending, "9") == "9"  # not a valid index → verbatim, model decides


def test_resolve_answer_free_text_passthrough() -> None:
    pending = Pending(fc_id="x")  # no options → free text / secret
    assert resolve_answer(pending, "sk-abc123") == "sk-abc123"


def test_resolve_answer_native_poll_vote_single() -> None:
    pending = Pending(fc_id="x", options=("Pizza", "Sushi", "Tacos"))
    assert resolve_answer(pending, _poll("Sushi")) == "Sushi"


def test_resolve_answer_native_poll_vote_multi_keeps_option_order() -> None:
    pending = Pending(fc_id="x", options=("Pizza", "Sushi", "Tacos"))
    # vote arrives Tacos-then-Pizza; answer is joined in the question's option order
    assert resolve_answer(pending, _poll("Tacos", "Pizza")) == "Pizza, Tacos"


def test_resolve_answer_numbered_multi_select() -> None:
    pending = Pending(fc_id="x", options=("Pizza", "Sushi", "Tacos"))
    assert resolve_answer(pending, "1,3") == "Pizza, Tacos"  # text-channel multi pick


def test_resolve_answer_poll_no_match_falls_through() -> None:
    pending = Pending(fc_id="x", options=("Pizza",))
    assert resolve_answer(pending, _poll("Sushi")) == _poll("Sushi")  # unknown hash → verbatim


# --- as_text: every connector can render a Question as numbered text -----------------


def test_question_renders_as_numbered_menu() -> None:
    rendered = as_text(Question(text="Which fruit?", options=("apple", "banana")))
    assert rendered == "Which fruit?\n  1. apple\n  2. banana\n(reply with the number)"


def test_free_text_question_is_just_its_text() -> None:
    assert as_text(Question(text="What's your API key?", secret=True)) == "What's your API key?"


# --- handler: pause surfaces the question, the next message resumes the run ----------


def _event(*texts: str, final: bool = True) -> SimpleNamespace:
    parts = [SimpleNamespace(text=t or None, function_call=None) for t in texts]
    return SimpleNamespace(
        content=SimpleNamespace(parts=parts),
        is_final_response=lambda: final,
        long_running_tool_ids=None,
    )


def _pause_event(
    fc_id: str,
    question: str,
    *,
    options: Any = None,
    secret: bool = False,
    multi: bool = False,
    preface: str = "",
) -> SimpleNamespace:
    """A fake event where the model called ask_user (a long-running pause)."""
    call = SimpleNamespace(
        id=fc_id,
        name="ask_user",
        args={"question": question, "options": options, "secret": secret, "multi": multi},
    )
    parts: list[Any] = []
    if preface:
        parts.append(SimpleNamespace(text=preface, function_call=None))
    parts.append(SimpleNamespace(text=None, function_call=call))
    return SimpleNamespace(
        content=SimpleNamespace(parts=parts),
        is_final_response=lambda: True,
        long_running_tool_ids={fc_id},
    )


class _FakeRunner:
    def __init__(self, events: list[SimpleNamespace]) -> None:
        self._events = events

    async def run_async(self, **_kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        for event in self._events:
            yield event


class _CapturingRunner:
    def __init__(self, events: list[SimpleNamespace]) -> None:
        self._events = events
        self.captured: dict[str, Any] = {}

    async def run_async(self, **kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        self.captured.update(kwargs)
        for event in self._events:
            yield event


async def _collect(handler: GaiaHandler, text: str) -> list[Any]:
    sent: list[Any] = []

    async def send(reply: Any) -> None:
        sent.append(reply)

    await handler(Inbound(text=text), send)
    return sent


async def test_pause_surfaces_question_and_records_pending() -> None:
    handler = GaiaHandler(SimpleNamespace(memory_service=None))
    handler._runner = _FakeRunner(
        [_pause_event("fc1", "Which fruit?", options=["apple", "banana"], preface="Let me ask.")]
    )

    sent = await _collect(handler, "surprise me")

    assert sent[0] == "Let me ask."  # preface text streamed first
    question = sent[1]
    assert isinstance(question, Question)
    assert question.text == "Which fruit?" and question.options == ("apple", "banana")
    assert handler._pending is not None and handler._pending.fc_id == "fc1"


async def test_multi_select_flag_flows_to_the_question() -> None:
    handler = GaiaHandler(SimpleNamespace(memory_service=None))
    handler._runner = _FakeRunner(
        [_pause_event("fc1", "Toppings?", options=["a", "b"], multi=True)]
    )

    sent = await _collect(handler, "build me a pizza")

    question = next(r for r in sent if isinstance(r, Question))
    assert question.multi is True


async def test_reply_resumes_the_run_with_a_function_response() -> None:
    handler = GaiaHandler(SimpleNamespace(memory_service=None))
    handler._runner = _FakeRunner(
        [_pause_event("fc1", "Which fruit?", options=["apple", "banana"])]
    )
    await _collect(handler, "surprise me")  # now paused on fc1

    resume = _CapturingRunner([_event("Great, banana it is.")])
    handler._runner = resume
    sent = await _collect(handler, "2")  # picks "banana"

    assert handler._pending is None  # answered → cleared
    assert sent == ["Great, banana it is."]
    fr = resume.captured["new_message"].parts[0].function_response
    assert fr.id == "fc1" and fr.name == "ask_user" and fr.response == {"answer": "banana"}
    # a function-response resume must not be echoed into the memory-ingest stream
    assert resume.captured["yield_user_message"] is False


async def test_secret_answer_is_not_buffered_to_memory() -> None:
    calls: list[dict[str, Any]] = []

    async def add_events_to_memory(**kwargs: Any) -> None:
        calls.append(kwargs)

    service = SimpleNamespace(add_events_to_memory=add_events_to_memory)
    memory = SimpleNamespace(auto_ingest=True, ingest_batch_size=1, ingest_interval_seconds=3600)
    gaia = SimpleNamespace(memory_service=service, config=SimpleNamespace(memory=memory))
    handler = GaiaHandler(gaia)

    handler._runner = _FakeRunner([_pause_event("fc1", "API key?", secret=True)])
    await _collect(handler, "I need to set it up")  # pause turn (no secret yet)
    handler._buffer = []  # ignore the (harmless) pause-turn buffering; isolate the answer turn

    handler._runner = _CapturingRunner([_event("Saved, thanks.")])
    await _collect(handler, "sk-secret-123")  # the secret answer

    assert handler._buffer == []  # the secret-bearing turn never entered the ingest buffer


class _CompletionTrackingRunner:
    """Records whether run_async ran to its natural end or was aclose()d early."""

    def __init__(self, events: list[SimpleNamespace]) -> None:
        self._events = events
        self.completed = False
        self.aclosed = False

    async def run_async(self, **_kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        try:
            for event in self._events:
                yield event
            self.completed = True  # reached StopAsyncIteration — the run closed itself
        except GeneratorExit:
            self.aclosed = True  # early break/return aclose()d a live span → the old bug
            raise


async def test_pause_lets_the_run_complete_not_cancelled() -> None:
    # Regression: a long-running ask_user emits no function-response, so run_async ends on
    # its own. The handler must iterate to that end, not return mid-loop — an early exit
    # aclose()s ADK's still-suspended generator ("Root node cancelled" / otel detach error).
    handler = GaiaHandler(SimpleNamespace(memory_service=None))
    runner = _CompletionTrackingRunner([_pause_event("fc1", "Pick?", options=["a", "b"])])
    handler._runner = runner

    await _collect(handler, "go")

    assert handler._pending is not None  # the pause was still recorded + surfaced
    assert runner.completed is True and runner.aclosed is False  # generator closed cleanly


async def test_reset_clears_a_pending_question() -> None:
    handler = GaiaHandler(SimpleNamespace(memory_service=None))
    handler._pending = Pending(fc_id="fc1", options=("a",))

    handler.reset_session()

    assert handler._pending is None
