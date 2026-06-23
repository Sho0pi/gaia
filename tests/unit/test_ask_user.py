"""Unit tests for the ask_user human-in-the-loop elicitation feature.

Covers three layers without a model backend: the tool (pauses by returning None +
flipping off summarization), the pure resolve/render logic, and the handler's
pause→surface→resume wiring (driven by a fake Runner that yields a long-running
ask_user call, then canned continuation events).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from gaia.connectors.base import Inbound, Question, as_text
from gaia.core.elicit import Pending, SoulPending, resolve_answer
from gaia.core.handler import GaiaHandler
from gaia.tools.ask_user import make_ask_user

# --- the tool: returns None to pause, turns off result summarization ----------------


def test_ask_user_pauses_and_skips_summarization() -> None:
    tool = make_ask_user()
    assert tool.is_long_running is True  # ADK marks the call so run_async completes (pauses)

    ctx = SimpleNamespace(actions=SimpleNamespace(skip_summarization=False))
    result = tool.func(question="pick one", options=["a", "b"], tool_context=ctx)

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


def test_soul_pending_json_round_trip() -> None:
    # P3 persists a SoulPending on a Task row; it must survive a JSON round-trip intact.
    from gaia.core.elicit import soul_pending_from_json, soul_pending_to_json

    p = SoulPending(
        warm_key="w/p",
        soul_key="w",
        project="p",
        soul_fc_id="fc",
        question="q?",
        options=("a", "b"),
        secret=True,
        soul_name="N",
        user_id="u",
        before={"a.html": 1.5},
    )
    back = soul_pending_from_json(soul_pending_to_json(p))
    assert back == p and back.options == ("a", "b")  # options restored as a tuple, not a list


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
    fc_id: str, question: str, *, options: Any = None, secret: bool = False, preface: str = ""
) -> SimpleNamespace:
    """A fake event where the model called ask_user (a long-running pause)."""
    call = SimpleNamespace(
        id=fc_id,
        name="ask_user",
        args={"question": question, "options": options, "secret": secret},
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


# --- P2: a delegated soul asks the user (delegate_to_soul pauses the root) -------------


def _delegate_pause_event(fc_id: str) -> SimpleNamespace:
    """A fake event where the root paused on a long-running delegate_to_soul call."""
    call = SimpleNamespace(id=fc_id, name="delegate_to_soul", args={})
    return SimpleNamespace(
        content=SimpleNamespace(parts=[SimpleNamespace(text=None, function_call=call)]),
        is_final_response=lambda: True,
        long_running_tool_ids={fc_id},
    )


class _DelegatePauseRunner:
    """Mimics a turn where delegate_to_soul paused: it appends the soul's pending to the
    handler-installed sink (as the real tool does) then yields the delegate pause event."""

    def __init__(self, fc_id: str, soul: SoulPending) -> None:
        self._fc_id = fc_id
        self._soul = soul

    async def run_async(self, **_kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        from gaia.core.elicit import soul_elicitation_sink

        sink = soul_elicitation_sink.get()
        if sink is not None:
            sink.append(self._soul)
        yield _delegate_pause_event(self._fc_id)


def _soul_pending(**kw: Any) -> SoulPending:
    base = dict(
        warm_key="web_designer/p",
        soul_key="web_designer",
        project="p",
        soul_fc_id="sfc",
        question="What's your API key?",
        soul_name="Web Designer",
        user_id="gaia-user",
    )
    return SoulPending(**{**base, **kw})


def _p2_gaia(unpinned: list[str] | None = None) -> SimpleNamespace:
    sink = unpinned if unpinned is not None else []
    return SimpleNamespace(
        memory_service=None,
        soul_sessions=SimpleNamespace(pin=lambda _k: None, unpin=lambda k: sink.append(k)),
    )


async def test_delegated_soul_pause_surfaces_prefixed_question() -> None:
    soul = _soul_pending(secret=True)
    handler = GaiaHandler(_p2_gaia())
    handler._runner = _DelegatePauseRunner("D", soul)

    sent = await _collect(handler, "build me an app")

    question = next(r for r in sent if isinstance(r, Question))
    assert question.text == "*Web Designer* asks: What's your API key?" and question.secret is True
    assert handler._pending is not None
    assert (
        handler._pending.soul is soul and handler._pending.fc_id == "D"
    )  # root paused on delegate


async def test_soul_answer_resumes_the_root_when_the_soul_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaia.souls.run import SoulRun

    unpinned: list[str] = []
    handler = GaiaHandler(_p2_gaia(unpinned))
    handler._runner = _DelegatePauseRunner("D", _soul_pending())
    await _collect(handler, "build me an app")  # paused on D, awaiting the soul

    async def fake_resume(_g: Any, pending: Any, answer: str) -> SoulRun:
        assert answer == "sk-1"  # the user's reply reached the soul
        return SoulRun(
            ok=True, soul_key="web_designer", soul_name="WD", created=False, summary="done sk-1"
        )

    monkeypatch.setattr("gaia.souls.run.resume_soul", fake_resume)
    resume = _CapturingRunner([_event("All set — your app is ready.")])
    handler._runner = resume

    sent = await _collect(handler, "sk-1")

    assert handler._pending is None and sent == ["All set — your app is ready."]
    fr = resume.captured["new_message"].parts[0].function_response
    assert fr.id == "D" and fr.name == "delegate_to_soul" and fr.response["summary"] == "done sk-1"
    assert resume.captured["yield_user_message"] is False
    assert unpinned == ["web_designer/p"]  # the soul's warm session is released


async def test_soul_can_ask_a_second_question_keeping_the_root_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gaia.souls.run import SoulRun

    handler = GaiaHandler(_p2_gaia())
    handler._runner = _DelegatePauseRunner("D", _soul_pending(soul_fc_id="sfc1", question="q1"))
    await _collect(handler, "build me an app")

    again = _soul_pending(soul_fc_id="sfc2", question="q2")

    async def fake_resume(_g: Any, _p: Any, _a: str) -> SoulRun:
        return SoulRun(
            ok=False, soul_key="web_designer", soul_name="WD", created=False, pending=again
        )

    monkeypatch.setattr("gaia.souls.run.resume_soul", fake_resume)
    resume = _CapturingRunner([_event("should not run")])
    handler._runner = resume

    sent = await _collect(handler, "first answer")

    question = next(r for r in sent if isinstance(r, Question))
    assert question.text == "*Web Designer* asks: q2"
    assert handler._pending is not None
    assert (
        handler._pending.soul is again and handler._pending.fc_id == "D"
    )  # still paused on delegate
    assert resume.captured == {}  # the root was NOT resumed — run_async never called
