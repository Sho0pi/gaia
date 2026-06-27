"""SessionWindowPlugin: trim replayed contents to the last N user turns, boundaries intact."""

from __future__ import annotations

from types import SimpleNamespace

from google.genai import types

from gaia.core.plugins import SessionWindowPlugin


def _user(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


def _model(text: str) -> types.Content:
    return types.Content(role="model", parts=[types.Part(text=text)])


def _call(name: str) -> types.Content:
    return types.Content(
        role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args={}))]
    )


def _resp(name: str) -> types.Content:
    # ADK tags a function_response with role="user" but no text — must NOT count as a turn.
    return types.Content(
        role="user",
        parts=[types.Part(function_response=types.FunctionResponse(name=name, response={}))],
    )


async def _trim(contents: list[types.Content], max_turns: int) -> list[types.Content]:
    req = SimpleNamespace(contents=list(contents))
    await SessionWindowPlugin(max_turns).before_model_callback(
        callback_context=None, llm_request=req
    )
    return req.contents


async def test_trims_to_last_n_user_turns() -> None:
    contents: list[types.Content] = []
    for i in range(5):
        contents += [_user(f"u{i}"), _model(f"m{i}")]

    out = await _trim(contents, max_turns=2)

    # last 2 user turns + their replies; nothing older
    assert [c.parts[0].text for c in out] == ["u3", "m3", "u4", "m4"]


async def test_within_window_is_unchanged() -> None:
    contents = [_user("u0"), _model("m0"), _user("u1"), _model("m1")]
    assert await _trim(contents, max_turns=5) == contents


async def test_function_response_is_not_counted_and_kept_whole() -> None:
    # One real user turn whose model reply calls a tool, then the tool result, then the final.
    contents = [
        _user("old"),
        _model("old reply"),
        _user("do a thing"),  # the turn we want to keep
        _call("mytool"),
        _resp("mytool"),
        _model("done"),
    ]

    out = await _trim(contents, max_turns=1)

    # sliced at the last *real* user message → the tool call/response pair stays intact, "old" drops
    kinds = [
        c.parts[0].text
        or (c.parts[0].function_call and "call")
        or (c.parts[0].function_response and "resp")
        for c in out
    ]
    assert kinds == ["do a thing", "call", "resp", "done"]
