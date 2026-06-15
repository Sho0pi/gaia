"""The ``remember`` tool writes a verbatim fact through ADK's public ``ToolContext``.

A fake ToolContext stands in for ADK's wiring so the dict contract + validation are checked
without a model or vector store. ``add_memory`` mirrors the real API: it records the call,
or raises ``ValueError`` when no memory service is configured.
"""

from __future__ import annotations

from typing import Any

from gaia.tools.remember import make_remember


class _FakeToolContext:
    """Stands in for ADK's ToolContext public surface used by ``remember``."""

    def __init__(self, *, has_memory: bool = True) -> None:
        self.has_memory = has_memory
        self.calls: list[dict[str, Any]] = []

    async def add_memory(self, *, memories: Any, custom_metadata: Any = None) -> None:
        if not self.has_memory:
            raise ValueError("Cannot add memory: memory service is not available.")
        self.calls.append({"memories": memories, "custom_metadata": custom_metadata})


async def test_remembers_fact() -> None:
    ctx = _FakeToolContext()
    remember = make_remember()

    result = await remember("  timezone is IST  ", tool_context=ctx)

    assert result == {"status": "success", "fact": "timezone is IST"}
    assert ctx.calls[0]["memories"][0].content.parts[0].text == "timezone is IST"


async def test_empty_fact_is_rejected() -> None:
    ctx = _FakeToolContext()
    remember = make_remember()

    result = await remember("   ", tool_context=ctx)

    assert result["status"] == "error"
    assert ctx.calls == []


async def test_errors_when_memory_disabled() -> None:
    remember = make_remember()

    result = await remember("a fact", tool_context=_FakeToolContext(has_memory=False))

    assert result["status"] == "error"
    assert "disabled" in result["error_message"]
