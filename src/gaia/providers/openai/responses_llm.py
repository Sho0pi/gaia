"""ADK model backend that runs over the ChatGPT subscription (Responses API).

ChatGPT OAuth tokens don't work against ``api.openai.com``; they call
``https://chatgpt.com/backend-api/codex/responses`` (the Responses API) with a
``chatgpt-account-id`` header. :class:`ChatGptOAuthLlm` is the ADK ``BaseLlm`` that maps
ADK's request/response to that backend, mirroring openclaw's
``src/llm/providers/openai-chatgpt-responses.ts``.

Scope: text + inbound images + function (tool) calls, with gpt-5.x reasoning items replayed
across turns (carried on a ``thought_signature`` part) so tool calls don't loop, and a tunable
reasoning ``effort`` (``reasoning.effort``). (A vision model is needed for images.) The wire
shape of this backend is unofficial and may change; everything backend-specific is kept in
this one module.

httpx is imported lazily (optional ``llm`` dep group).
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import BaseModel

from gaia.providers.openai import device_auth
from gaia.providers.openai.store import Credentials, load_credentials

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.models.llm_request import LlmRequest

logger = logging.getLogger(__name__)

RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"

#: Cap on any single string inside a replayed tool result, and on the whole serialized output.
#: A tool like ``browser_take_screenshot`` returns the image inline (a base64 block); replaying
#: that blob into every later turn's history bloats the request until the model returns nothing
#: (a reasoning-only, message-less completion -> a dead chat). The image is already delivered to
#: the user from the live turn, so the model never needs the bytes in history.
_MAX_STR = 2000
_MAX_TOOL_OUTPUT = 16000
#: Keys whose value is a binary payload to drop wholesale (mcp image block ``data``, etc.).
_BINARY_KEYS = frozenset({"data", "image", "image_url", "b64_json", "bytes", "blob"})


class ChatGptNotAuthenticatedError(RuntimeError):
    """Raised when no ChatGPT OAuth credentials are stored."""


def _jsonable(obj: Any) -> Any:
    """Fallback for ``json.dumps``: pydantic models (and other objects) -> JSON-safe data.

    ADK tool results are often pydantic objects (e.g. ``load_memory`` returns a
    ``LoadMemoryResponse``), which plain ``json.dumps`` can't serialize.
    """
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return str(obj)


def _dumps(value: Any) -> str:
    """``json.dumps`` that never raises on a tool's pydantic/odd return value."""
    return json.dumps(value, default=_jsonable)


def _call_args(arguments: str | None) -> dict[str, Any]:
    """Parse a function-call ``arguments`` JSON, dropping null values.

    gpt-5.x emits an explicit ``null`` for an *omitted* optional tool arg (Gemini just leaves it
    out). Passing that ``None`` through to ADK would override the tool's default (e.g. ``""``) and
    crash its string ops — so drop nulls here and let the default apply, matching Gemini.
    """
    return {key: value for key, value in json.loads(arguments or "{}").items() if value is not None}


def _shrink(value: Any) -> Any:
    """Drop/truncate heavy bits of a tool result so the replayed history stays small.

    Binary payloads (a screenshot's inline base64) are dropped to a placeholder; any long
    string is truncated. Keeps the structure so the model still sees what the tool returned.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if k in _BINARY_KEYS and isinstance(v, str) and len(v) > _MAX_STR:
                out[k] = f"[{len(v)} chars omitted]"
            else:
                out[k] = _shrink(v)
        return out
    if isinstance(value, list):
        return [_shrink(v) for v in value]
    if isinstance(value, str) and len(value) > _MAX_STR:
        return value[:_MAX_STR] + f"…[{len(value) - _MAX_STR} more chars omitted]"
    return value


def _tool_output(response: Any) -> str:
    """Serialize a tool result for replay, shrunk + capped so it can't bloat history."""
    text = _dumps(_shrink(response or {}))
    if len(text) > _MAX_TOOL_OUTPUT:
        text = text[:_MAX_TOOL_OUTPUT] + "…[truncated]"
    return text


def _content_to_input(contents: list[types.Content]) -> list[dict[str, Any]]:
    """Map ADK contents to Responses ``input`` items (text + function call/result).

    The backend 400s ("No tool output found for function call <id>") if any
    ``function_call`` lacks a matching ``function_call_output``. A turn cancelled
    mid-flight (e.g. WhatsApp socket drop) persists the call event but not its
    output, which would brick the session permanently on every later turn — so we
    synthesize a placeholder output for any orphaned call to keep history valid.
    """
    items: list[dict[str, Any]] = []
    call_ids: set[str] = set()
    answered_ids: set[str] = set()
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
            elif part.inline_data and part.inline_data.data:
                mime = part.inline_data.mime_type or "image/jpeg"
                if mime.startswith("image/"):
                    # An inbound image. This is OpenAI's Responses wire format (input_image +
                    # base64 data URL) — every provider differs (Claude uses source/base64,
                    # Gemini inline_data); we convert here only because this is our own backend.
                    # An image-only turn would otherwise produce no input items -> 400.
                    b64 = base64.b64encode(bytes(part.inline_data.data)).decode("ascii")
                    block: dict[str, Any] = {
                        "type": "input_image",
                        "image_url": f"data:{mime};base64,{b64}",
                    }
                else:
                    # Video/audio/PDF: this backend has no vision for them. Don't drop the part
                    # silently (an attachment-only turn would then have no input -> 400); send a
                    # text note so the turn stays valid and the model can say it can't view it.
                    block = {
                        "type": "input_text",
                        "text": f"[the user sent a {mime} file, which this model can't open]",
                    }
                items.append({"type": "message", "role": role, "content": [block]})
            elif part.function_call:
                call_id = part.function_call.id or part.function_call.name or ""
                call_ids.add(call_id)
                items.append(
                    {
                        "type": "function_call",
                        "name": part.function_call.name,
                        "call_id": call_id,
                        "arguments": _dumps(dict(part.function_call.args or {})),
                    }
                )
            elif part.function_response:
                call_id = part.function_response.id or part.function_response.name or ""
                answered_ids.add(call_id)
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": _tool_output(part.function_response.response),
                    }
                )
    # Heal orphaned calls (interrupted turns) so the backend doesn't 400 the session.
    for call_id in call_ids - answered_ids:
        items.append(
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": _dumps({"error": "tool call interrupted; no output recorded"}),
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


def _output_format(llm_request: LlmRequest) -> dict[str, Any] | None:
    """The Responses ``text.format`` for an agent's ``output_schema``, else None.

    ADK's ``set_output_schema`` stores the pydantic model on ``config.response_schema``. Off Gemini
    that schema was previously dropped (the model only saw it as instruction text → flaky JSON);
    passing it as a json_schema format constrains the output. ``strict=False`` because our schemas
    carry defaults/optional fields, which strict mode rejects.
    """
    schema = getattr(llm_request.config, "response_schema", None)
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return {
            "type": "json_schema",
            "name": schema.__name__,
            "schema": _json_schema(schema.model_json_schema()),
            "strict": False,
        }
    return None


class ChatGptOAuthLlm(BaseLlm):
    """Run an LLM turn over the user's ChatGPT subscription (Responses backend)."""

    #: Reasoning effort (minimal|low|medium|high); blank = the model's default. Sent as the
    #: Responses ``reasoning.effort`` so the gpt-5.x backend thinks harder or faster on demand.
    effort: str = ""

    @staticmethod
    def supported_models() -> list[str]:
        return [r"openai-chatgpt/.*", r"chatgpt/.*"]

    def _model_id(self) -> str:
        return self.model.split("/", 1)[1] if "/" in self.model else self.model

    def _request_body(self, llm_request: LlmRequest, session_id: str) -> dict[str, Any]:
        """The Responses request body. Shape matches openclaw's working Codex request
        (buildRequestBody): the backend 400s without text/include/tool_choice/
        parallel_tool_calls/prompt_cache_key, and rejects an empty instructions string or an
        empty tools array. ``reasoning.effort`` is added only when an effort is set.
        """
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
        if self.effort:
            body["reasoning"] = {"effort": self.effort}
        fmt = _output_format(llm_request)
        if fmt is not None:
            body["text"]["format"] = fmt  # constrain output to the agent's output_schema
        tools = _tools_from_request(llm_request)
        if tools:
            body["tools"] = tools
        return body

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        import httpx

        creds = load_credentials()
        if creds is None:
            raise ChatGptNotAuthenticatedError("no ChatGPT login — run: gaia model")

        session_id = str(uuid.uuid4())
        body = self._request_body(llm_request, session_id)

        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as client:
            creds = await self._ensure_fresh(creds, client)
            # Retry once on a transient stream drop (httpx TransportError: ReadError/ConnectError/
            # protocol/timeout) — but only if nothing was emitted yet, so a mid-stream drop can't
            # duplicate already-delivered text. A clean blip recovers without the user seeing it.
            for attempt in range(2):
                produced = False
                try:
                    async for resp in self._stream(client, creds, body, session_id, stream):
                        produced = True
                        yield resp
                    return
                except httpx.TransportError as exc:
                    if produced or attempt == 1:
                        raise
                    logger.warning(
                        "ChatGPT stream dropped (%s) before any output — retrying once",
                        type(exc).__name__,
                    )

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
        # Whether any text was delivered incrementally as partial deltas. When the backend
        # sends the answer only as a completed message item (seen with reasoning effort on) we
        # recover it below, and it must still be put in the final response.
        streamed = False
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
                        streamed = True
                        yield LlmResponse(
                            content=types.Content(role="model", parts=[types.Part(text=delta)]),
                            partial=True,
                        )
                elif kind == "response.output_item.done":
                    item = event.get("item", {})
                    if item.get("type") == "message" and not text_parts:
                        # The assistant message arrived as a completed item, not as
                        # output_text.delta events (the backend does this with reasoning effort
                        # on). Recover its text so the turn isn't empty. Guarded on no deltas so
                        # a normal streamed turn isn't double-counted.
                        text_parts.extend(
                            c.get("text", "")
                            for c in item.get("content") or []
                            if c.get("type") in ("output_text", "text") and c.get("text")
                        )
                    elif item.get("type") == "function_call":
                        calls.append(
                            types.Part(
                                function_call=types.FunctionCall(
                                    id=item.get("call_id"),
                                    name=item.get("name"),
                                    args=_call_args(item.get("arguments")),
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
        # Add the text unless it was already delivered as partial deltas (stream mode). Text
        # recovered from a message item was never streamed, so it still needs to go out here.
        if full and not streamed:
            parts.append(types.Part(text=full))
        parts.extend(calls)
        # A turn with neither a message nor a tool call (reasoning only) reaches the user as an
        # empty "(Done — nothing to add)" — the dead-chat symptom. Usually a sign history has
        # grown too big (see _tool_output). Log it so the failure is visible if it recurs.
        if not full and not calls:
            logger.warning(
                "openai turn produced no message or tool call (reasoning-only) — "
                "model=%s, reasoning_items=%d",
                self._model_id(),
                len(reasoning),
            )
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
