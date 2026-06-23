"""ChatGptOAuthLlm: maps an ADK request to the Responses backend and back."""

from __future__ import annotations

import time
from typing import Any

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

import gaia.providers.openai.responses_llm as rl
from gaia.providers.openai.responses_llm import (
    ChatGptNotAuthenticatedError,
    ChatGptOAuthLlm,
    _content_to_input,
    _tools_from_request,
)
from gaia.providers.openai.store import Credentials


def test_tool_schema_types_are_lowercased_json_schema() -> None:
    decl = types.FunctionDeclaration(
        name="get_price",
        parameters=types.Schema(
            type="OBJECT", properties={"c": types.Schema(type="STRING")}, required=["c"]
        ),
    )
    req = LlmRequest(
        config=types.GenerateContentConfig(tools=[types.Tool(function_declarations=[decl])])
    )

    params = _tools_from_request(req)[0]["parameters"]

    assert params["type"] == "object"  # not the genai enum "OBJECT"
    assert params["properties"]["c"]["type"] == "string"


def test_request_body_includes_reasoning_effort_when_set() -> None:
    req = LlmRequest(
        contents=[types.Content(role="user", parts=[types.Part(text="hi")])],
        config=types.GenerateContentConfig(),
    )

    high = ChatGptOAuthLlm(model="gpt-5.5", effort="high")._request_body(req, "sid")
    assert high["reasoning"] == {"effort": "high"}

    default = ChatGptOAuthLlm(model="gpt-5.5")._request_body(req, "sid")
    assert "reasoning" not in default  # no effort -> field omitted


def test_function_response_with_pydantic_payload_is_serializable() -> None:
    import json

    from pydantic import BaseModel

    class _ToolResult(BaseModel):  # mimics ADK's LoadMemoryResponse
        memories: list[str] = []

    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="c1", name="load_memory", response={"r": _ToolResult(memories=["x"])}
                    )
                )
            ],
        )
    ]

    item = _content_to_input(contents)[0]  # must not raise on the pydantic value

    assert item["type"] == "function_call_output"
    assert json.loads(item["output"]) == {"r": {"memories": ["x"]}}


def test_screenshot_base64_is_dropped_from_replayed_history() -> None:
    # A browser_take_screenshot result carries the image inline (base64). Replaying that blob
    # every turn bloats history until the model returns nothing (dead chat). It must be dropped
    # to a placeholder — the image already reached the user from the live turn.

    big = "A" * 50000  # a base64 image payload
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="c1",
                        name="browser_take_screenshot",
                        response={"content": [{"type": "image", "data": big, "mime": "image/png"}]},
                    )
                )
            ],
        )
    ]

    item = _content_to_input(contents)[0]

    assert item["type"] == "function_call_output"
    assert big not in item["output"]  # the blob is gone
    assert "omitted" in item["output"]  # replaced by a placeholder
    assert len(item["output"]) < 1000  # history stays small


def test_huge_tool_output_is_capped() -> None:
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id="c1", name="t", response={"text": "Z" * 100000}
                    )
                )
            ],
        )
    ]

    item = _content_to_input(contents)[0]

    assert len(item["output"]) < 20000  # capped, not the full 100k


def test_reasoning_part_is_replayed_as_a_reasoning_item() -> None:
    import json

    sig = json.dumps({"id": "rs_1", "encrypted_content": "enc"}).encode()
    contents = [
        types.Content(role="model", parts=[types.Part(thought=True, thought_signature=sig)])
    ]

    item = _content_to_input(contents)[0]

    assert item == {"type": "reasoning", "id": "rs_1", "encrypted_content": "enc", "summary": []}


def test_inbound_image_becomes_an_input_image_item() -> None:
    # An image-only turn must produce a real input item (else the backend 400s "missing input").
    contents = [
        types.Content(
            role="user",
            parts=[types.Part.from_bytes(data=b"\xff\xd8\xffjpeg", mime_type="image/jpeg")],
        )
    ]

    item = _content_to_input(contents)[0]

    assert item["type"] == "message" and item["role"] == "user"
    img = item["content"][0]
    assert img["type"] == "input_image"
    assert img["image_url"].startswith("data:image/jpeg;base64,")


def test_non_image_inline_data_becomes_a_text_note() -> None:
    # This backend can't view video/audio/PDF; the part must become a text note (not be
    # dropped — an attachment-only turn would otherwise have no input and 400).
    contents = [
        types.Content(
            role="user",
            parts=[types.Part.from_bytes(data=b"%PDF-1.4", mime_type="application/pdf")],
        )
    ]

    item = _content_to_input(contents)[0]

    assert item["type"] == "message"
    content = item["content"][0]
    assert content["type"] == "input_text" and "application/pdf" in content["text"]


def test_orphaned_function_call_gets_synthetic_output() -> None:
    # A turn cancelled mid-flight leaves a function_call with no matching response.
    # Without healing, the backend 400s ("No tool output found") on every later turn.
    contents = [
        types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(id="call_x", name="t", args={}))],
        )
    ]

    items = _content_to_input(contents)

    assert items[0]["type"] == "function_call"
    output = next(i for i in items if i["type"] == "function_call_output")
    assert output["call_id"] == "call_x"


def test_answered_function_call_gets_no_synthetic_output() -> None:
    contents = [
        types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(id="call_x", name="t", args={}))],
        ),
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(id="call_x", name="t", response={})
                )
            ],
        ),
    ]

    outputs = [i for i in _content_to_input(contents) if i["type"] == "function_call_output"]

    assert len(outputs) == 1  # no synthetic duplicate


_SSE = [
    'data: {"type":"response.output_text.delta","delta":"Hello"}',
    'data: {"type":"response.output_text.delta","delta":" world"}',
    'data: {"type":"response.output_item.done","item":{"type":"function_call",'
    '"name":"web_search","call_id":"c1","arguments":"{\\"q\\":\\"x\\"}"}}',
    "data: [DONE]",
]


class _FakeStream:
    def __init__(self, lines: list[str]) -> None:
        self._lines, self.status_code = lines, 200

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False

    def raise_for_status(self) -> None:
        pass

    async def aiter_lines(self) -> Any:
        for line in self._lines:
            yield line


class _FakeClient:
    def __init__(self, *_a: Any, **_k: Any) -> None:
        self.body: dict[str, Any] | None = None

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False

    def stream(self, method: str, url: str, *, headers: Any, json: dict[str, Any]) -> _FakeStream:
        self.body = json
        return _FakeStream(_SSE)


def _request() -> LlmRequest:
    return LlmRequest(
        model="gpt-5",
        contents=[types.Content(role="user", parts=[types.Part(text="hi")])],
        config=types.GenerateContentConfig(system_instruction="be brief"),
    )


@pytest.fixture
def fresh_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    creds = Credentials(
        access_token="at", refresh_token="rt", account_id="acc", expires_at=time.time() + 9999
    )
    monkeypatch.setattr(rl, "load_credentials", lambda *a, **k: creds)


async def test_streams_text_and_function_call(
    monkeypatch: pytest.MonkeyPatch, fresh_creds: None
) -> None:
    captured: dict[str, Any] = {}

    def fake_client(*a: Any, **k: Any) -> _FakeClient:
        c = _FakeClient()
        captured["client"] = c
        return c

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    out = [r async for r in ChatGptOAuthLlm(model="gpt-5").generate_content_async(_request())]

    # request body mapping
    body = captured["client"].body
    assert body["model"] == "gpt-5"
    assert body["instructions"] == "be brief"
    assert body["input"][0]["content"][0]["text"] == "hi"

    # final response carries the joined text + the function call
    final = out[-1]
    parts = final.content.parts
    assert any(p.text == "Hello world" for p in parts)
    assert any(p.function_call and p.function_call.name == "web_search" for p in parts)


class _RaisingStream(_FakeStream):
    """A stream that drops (httpx.ReadError) before emitting any line."""

    def __init__(self) -> None:
        super().__init__([])

    async def aiter_lines(self) -> Any:
        import httpx

        raise httpx.ReadError("connection reset")
        yield ""  # pragma: no cover - unreachable; makes this an async generator


async def test_retries_once_on_transient_stream_drop(
    monkeypatch: pytest.MonkeyPatch, fresh_creds: None
) -> None:
    import httpx

    class _FlakyClient(_FakeClient):
        calls = 0

        def stream(self, method: str, url: str, *, headers: Any, json: dict[str, Any]) -> Any:
            self.body = json
            type(self).calls += 1
            return _RaisingStream() if type(self).calls == 1 else _FakeStream(_SSE)

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FlakyClient())

    out = [r async for r in ChatGptOAuthLlm(model="gpt-5").generate_content_async(_request())]

    assert _FlakyClient.calls == 2  # dropped once, retried, then succeeded
    assert any(p.text == "Hello world" for p in out[-1].content.parts)  # recovered output


async def test_no_retry_after_partial_output(
    monkeypatch: pytest.MonkeyPatch, fresh_creds: None
) -> None:
    import httpx

    class _PartialThenDrop(_FakeStream):
        def __init__(self) -> None:
            super().__init__([])

        async def aiter_lines(self) -> Any:
            yield 'data: {"type":"response.output_text.delta","delta":"Hi"}'
            raise httpx.ReadError("reset mid-stream")

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _make_client(_PartialThenDrop()))

    # stream=True so the delta is yielded before the drop → already produced output → no retry.
    llm = ChatGptOAuthLlm(model="gpt-5")
    with pytest.raises(httpx.ReadError):
        async for _ in llm.generate_content_async(_request(), stream=True):
            pass


def _make_client(stream_obj: Any) -> Any:
    client = _FakeClient()
    client.stream = lambda *a, **k: stream_obj  # type: ignore[method-assign]
    return client


async def test_recovers_text_from_a_completed_message_item(
    monkeypatch: pytest.MonkeyPatch, fresh_creds: None
) -> None:
    # With reasoning effort on, the backend delivers the answer as a completed message item
    # instead of output_text.delta events. The turn must still carry that text (not be empty).
    lines = [
        'data: {"type":"response.output_item.done","item":{"type":"message",'
        '"role":"assistant","content":[{"type":"output_text","text":"Recovered answer"}]}}',
        "data: [DONE]",
    ]

    class _MsgClient(_FakeClient):
        def stream(self, *a: Any, **k: Any) -> _FakeStream:
            return _FakeStream(lines)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _MsgClient())

    out = [r async for r in ChatGptOAuthLlm(model="gpt-5").generate_content_async(_request())]

    assert any(p.text == "Recovered answer" for p in out[-1].content.parts)


async def test_missing_credentials_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rl, "load_credentials", lambda *a, **k: None)

    with pytest.raises(ChatGptNotAuthenticatedError, match="auth openai"):
        async for _ in ChatGptOAuthLlm(model="gpt-5").generate_content_async(_request()):
            pass
