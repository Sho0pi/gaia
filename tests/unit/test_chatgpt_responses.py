"""ChatGptOAuthLlm: maps an ADK request to the Responses backend and back."""

from __future__ import annotations

import time
from typing import Any

import pytest
from google.adk.models.llm_request import LlmRequest
from google.genai import types

import godpy.providers.openai_chatgpt.responses_llm as rl
from godpy.providers.openai_chatgpt.responses_llm import (
    ChatGptNotAuthenticatedError,
    ChatGptOAuthLlm,
)
from godpy.providers.openai_chatgpt.store import Credentials

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


async def test_missing_credentials_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rl, "load_credentials", lambda *a, **k: None)

    with pytest.raises(ChatGptNotAuthenticatedError, match="auth openai-chatgpt"):
        async for _ in ChatGptOAuthLlm(model="gpt-5").generate_content_async(_request()):
            pass
