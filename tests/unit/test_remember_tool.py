"""The ``remember`` tool writes a verbatim fact through the Runner's memory service.

A fake invocation context stands in for ADK's wiring so the dict contract, validation
are checked without a model or vector store.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from godpy.tools.remember import make_remember


class _FakeMemoryService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def add_memory(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _tool_context(memory_service: Any) -> SimpleNamespace:
    invocation = SimpleNamespace(memory_service=memory_service, app_name="godpy", user_id="u1")
    return SimpleNamespace(_invocation_context=invocation)


async def test_remembers_fact() -> None:
    service = _FakeMemoryService()
    remember = make_remember()

    result = await remember("  timezone is IST  ", tool_context=_tool_context(service))

    assert result == {"status": "success", "fact": "timezone is IST"}
    call = service.calls[0]
    assert call["user_id"] == "u1" and call["app_name"] == "godpy"
    assert call["memories"][0].content.parts[0].text == "timezone is IST"


async def test_empty_fact_is_rejected() -> None:
    service = _FakeMemoryService()
    remember = make_remember()

    result = await remember("   ", tool_context=_tool_context(service))

    assert result["status"] == "error"
    assert service.calls == []


async def test_errors_when_memory_disabled() -> None:
    remember = make_remember()

    result = await remember("a fact", tool_context=_tool_context(None))

    assert result["status"] == "error"
    assert "disabled" in result["error_message"]
