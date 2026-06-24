"""Shared test doubles: a scripted model + a recording connector + LlmResponse factories.

Centralizes the ``FakeLlm`` / ``_Sender`` / ``_text``/``_call``/``_forge``/``_reuse`` copies that
were duplicated across the mission / soul / cron / turn tests. Importable as ``from _fakes import
…`` (``pythonpath = ["tests"]`` in pyproject).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from gaia.agents import AgentSpec
from gaia.souls.smith import SoulDecision


class FakeLlm(BaseLlm):
    """Scripted model: yields one canned ``LlmResponse`` per call, in order.

    Default pops the next response and raises ``IndexError`` if over-consumed (catches a
    miscounted script). ``repeat_last=True`` keeps yielding the final response once the script
    is down to one, so a best-effort follow-up turn doesn't crash a board-state assertion.
    """

    model: str = "fake-model"
    responses: list[LlmResponse]
    repeat_last: bool = False
    calls: int = 0

    async def generate_content_async(
        self, llm_request: Any, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.calls += 1
        if self.repeat_last and len(self.responses) == 1:
            yield self.responses[0]
        else:
            yield self.responses.pop(0)


class FakeSender:
    """A stand-in connector that records what was sent. ``sent`` holds ``(chat, reply)`` pairs."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []

    async def send_to(self, chat: str, reply: Any) -> None:
        self.sent.append((chat, reply))

    @property
    def texts(self) -> list[str]:
        """Just the replies, with a non-text (``Media``) reply rendered as ``[media <path>]``."""
        return [
            r if isinstance(r, str) else f"[media {getattr(r, 'path', r)}]" for _, r in self.sent
        ]


def text_response(text: str) -> LlmResponse:
    """A model turn that emits plain ``text``."""
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


def call_response(name: str, **args: Any) -> LlmResponse:
    """A model turn that calls tool ``name`` with ``args``."""
    part = types.Part(function_call=types.FunctionCall(name=name, args=args))
    return LlmResponse(content=types.Content(role="model", parts=[part]))


def forge_response(name: str) -> LlmResponse:
    """A soul-smith decision that forges a new soul named ``name``."""
    return text_response(
        SoulDecision(
            action="forge",
            reason=f"need a {name}",
            spec=AgentSpec(name=name, description=f"a {name}", instruction="Do it.", model="fake"),
        ).model_dump_json()
    )


def reuse_response(key: str) -> LlmResponse:
    """A soul-smith decision that reuses the existing soul ``key``."""
    decision = SoulDecision(action="reuse", reason="fits", soul_key=key)
    return text_response(decision.model_dump_json())
