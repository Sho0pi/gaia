"""ADK model backend that runs over the ChatGPT subscription (Responses API).

ChatGPT OAuth tokens don't work against ``api.openai.com``; they call
``https://chatgpt.com/backend-api/codex/responses`` (the Responses API) with a
``chatgpt-account-id`` header. :class:`ChatGptOAuthLlm` is the ADK ``BaseLlm`` that maps
ADK's request/response to that backend, mirroring openclaw's
``src/llm/providers/openai-chatgpt-responses.ts``.

Scope: text + function (tool) calls, with gpt-5.x reasoning items replayed across turns
(carried on a ``thought_signature`` part) so tool calls don't loop. Inline images are out
of scope. The wire shape of this backend is unofficial and may change; everything
backend-specific is kept in this one module.

httpx is imported lazily (optional ``llm`` dep group).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from godpy.providers.openai import device_auth
from godpy.providers.openai.store import Credentials, load_credentials

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.models.llm_request import LlmRequest

RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"


class ChatGptNotAuthenticatedError(RuntimeError):
    """Raised when no ChatGPT OAuth credentials are stored."""


def _content_to_input(contents: list[types.Content]) -> list[dict[str, Any]]:
    """Map ADK contents to Responses ``input`` items (text + function call/result)."""
    items: list[dict[str, Any]] = []
    for content in contents:
        role = "assistant" if content.role == "model" else (content.role or "user")
        for part in content.parts or []:
            if part.thought_signature:
                # gpt-5.x is stateless here (store:false): its encrypted reasoning must be
                # replayed verbatim before the call it led to, or the model re-issues it.
                reasoning = json.loads(bytes(part.thought_signature).decode("utf-8"))
                items.append(
                    {
                        "type": "reasoning",
                        "id": reasoning["id"],
                        "encrypted_content": reasoning["encrypted_content"],
                        "summary": [],
                    }
                )
            elif part.text:
                kind = "output_text" if role == "assistant" else "input_text"
                items.append(
                    {
                        "type": "message",
                        "role": role,
                        "content": [{"type": kind, "text": part.text}],
                    }
                )
            elif part.function_call:
                items.append(
                    {
                        "type": "function_call",
                        "name": part.function_call.name,
                        "call_id": part.function_call.id or part.function_call.name,
                        "arguments": json.dumps(dict(part.function_call.args or {})),
                    }
                )
            elif part.function_response:
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": part.function_response.id or part.function_response.name,
                        "output": json.dumps(part.function_response.response or {}),
                    }
                )
    return items


def _json_schema(node: Any) -> Any:
    """Normalize a genai schema dump to JSON Schema: lowercase the enum-valued ``type``."""
    if isinstance(node, dict):
        return {
            k: (v.lower() if k == "type" and isinstance(v, str) else _json_schema(v))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_json_schema(v) for v in node]
    return node


def _tools_from_request(llm_request: LlmRequest) -> list[dict[str, Any]]:
    """Map the request's function declarations to Responses tool defs."""
    tools: list[dict[str, Any]] = []
    for tool in getattr(llm_request.config, "tools", None) or []:
        for decl in getattr(tool, "function_declarations", None) or []:
            schema = (
                _json_schema(decl.parameters.model_dump(exclude_none=True))
                if decl.parameters
                else {}
            )
            schema.setdefault("type", "object")
            tools.append(
                {
                    "type": "function",
                    "name": decl.name,
                    "description": decl.description or "",
                    "parameters": schema,
                }
            )
    return tools


def _system_text(llm_request: LlmRequest) -> str:
    si = getattr(llm_request.config, "system_instruction", None)
    if isinstance(si, str):
        return si
    if isinstance(si, types.Content):
        return "".join(p.text for p in si.parts or [] if p.text)
    return ""


class ChatGptOAuthLlm(BaseLlm):
    """Run an LLM turn over the user's ChatGPT subscription (Responses backend)."""

    @staticmethod
    def supported_models() -> list[str]:
        return [r"openai-chatgpt/.*", r"chatgpt/.*"]

    def _model_id(self) -> str:
        return self.model.split("/", 1)[1] if "/" in self.model else self.model

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        import httpx

        creds = load_credentials()
        if creds is None:
            raise ChatGptNotAuthenticatedError("no ChatGPT login — run: python main.py auth openai")

        # Body shape matches openclaw's working Codex request (buildRequestBody): the
        # backend 400s without text/include/tool_choice/parallel_tool_calls/prompt_cache_key,
        # and rejects an empty instructions string or an empty tools array.
        session_id = str(uuid.uuid4())
        body: dict[str, Any] = {
            "model": self._model_id(),
            "instructions": _system_text(llm_request) or "You are a helpful assistant.",
            "input": _content_to_input(llm_request.contents),
            "tool_choice": "auto",
            "parallel_tool_calls": True,
            "store": False,
            "stream": True,
            "text": {"verbosity": "low"},
            "include": ["reasoning.encrypted_content"],
            "prompt_cache_key": session_id,
        }
        tools = _tools_from_request(llm_request)
        if tools:
            body["tools"] = tools

        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as client:
            creds = await self._ensure_fresh(creds, client)
            async for resp in self._stream(client, creds, body, session_id, stream):
                yield resp

    async def _ensure_fresh(self, creds: Credentials, client: Any) -> Credentials:
        if creds.is_expired():
            creds = await device_auth.refresh(creds, client=client)
            creds.save()
        return creds

    def _headers(self, creds: Credentials, session_id: str) -> dict[str, str]:
        # Mirrors openclaw's buildSSEHeaders. originator must match the Codex client id;
        # accept=text/event-stream + the request-id headers are required by the backend.
        return {
            "Authorization": f"Bearer {creds.access_token}",
            "chatgpt-account-id": creds.account_id,
            "content-type": "application/json",
            "accept": "text/event-stream",
            "OpenAI-Beta": "responses=experimental",
            "originator": "codex_cli_rs",
            "session_id": session_id,
            "x-client-request-id": session_id,
            "User-Agent": "codex_cli_rs/0.0.0",
        }

    async def _stream(
        self, client: Any, creds: Credentials, body: dict[str, Any], session_id: str, stream: bool
    ) -> AsyncGenerator[LlmResponse, None]:
        text_parts: list[str] = []
        calls: list[types.Part] = []
        reasoning: list[types.Part] = []
        async with client.stream(
            "POST", RESPONSES_URL, headers=self._headers(creds, session_id), json=body
        ) as response:
            if response.status_code == 401:
                # token rejected mid-flight: refresh once and retry on the next turn
                refreshed = await device_auth.refresh(creds, client=client)
                refreshed.save()
                raise ChatGptNotAuthenticatedError("ChatGPT token refreshed — retry the request")
            if response.status_code >= 400:
                detail = (await response.aread()).decode("utf-8", "replace")[:800]
                raise RuntimeError(f"ChatGPT Responses {response.status_code}: {detail}")
            async for line in response.aiter_lines():
                event = _parse_sse(line)
                if event is None:
                    continue
                kind = event.get("type")
                if kind == "response.output_text.delta":
                    delta = event.get("delta", "")
                    text_parts.append(delta)
                    if stream and delta:
                        yield LlmResponse(
                            content=types.Content(role="model", parts=[types.Part(text=delta)]),
                            partial=True,
                        )
                elif kind == "response.output_item.done":
                    item = event.get("item", {})
                    if item.get("type") == "function_call":
                        calls.append(
                            types.Part(
                                function_call=types.FunctionCall(
                                    id=item.get("call_id"),
                                    name=item.get("name"),
                                    args=json.loads(item.get("arguments") or "{}"),
                                )
                            )
                        )
                    elif item.get("type") == "reasoning" and item.get("encrypted_content"):
                        # Carry the encrypted reasoning across turns on a thought part so
                        # _content_to_input can replay it (prevents the tool-call loop).
                        sig = json.dumps(
                            {"id": item["id"], "encrypted_content": item["encrypted_content"]}
                        ).encode("utf-8")
                        reasoning.append(types.Part(thought=True, thought_signature=sig))

        # Reasoning first, then text, then the tool calls it led to — the order the
        # backend expects when these items are replayed next turn.
        parts: list[types.Part] = [*reasoning]
        full = "".join(text_parts)
        if full and not stream:
            parts.append(types.Part(text=full))
        parts.extend(calls)
        if parts or not stream:
            yield LlmResponse(
                content=types.Content(role="model", parts=parts or [types.Part(text=full)]),
                turn_complete=True,
            )


def _parse_sse(line: str) -> dict[str, Any] | None:
    """Parse one SSE ``data:`` line into a JSON event, or None for keep-alives."""
    line = line.strip()
    if not line.startswith("data:"):
        return None
    payload = line[len("data:") :].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        parsed = json.loads(payload)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None
