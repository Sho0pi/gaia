"""ToolLoggingPlugin logs tool calls that don't self-log (e.g. ADK built-ins)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from godpy.god.plugins import ToolLoggingPlugin


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr("godpy.god.plugins.log_event", lambda a, **k: events.append((a, k)))
    return events


def _tool(name: str) -> Any:
    return SimpleNamespace(name=name)


async def test_logs_builtin_tool(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin({"web_search"})  # web_search self-logs

    await plugin.after_tool_callback(
        tool=_tool("load_memory"), tool_args={}, tool_context=None, result={"memories": []}
    )

    assert logged == [("tool_used", {"tool": "load_memory", "status": "ok"})]


async def test_passes_through_dict_status(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin(set())

    await plugin.after_tool_callback(
        tool=_tool("some_tool"), tool_args={}, tool_context=None, result={"status": "success"}
    )

    assert logged[0][1]["status"] == "success"


async def test_skips_self_logging_tool(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin({"web_search"})

    await plugin.after_tool_callback(
        tool=_tool("web_search"), tool_args={}, tool_context=None, result={"status": "success"}
    )

    assert logged == []  # the tool already logged itself; no duplicate


async def test_logs_tool_error(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin({"web_search"})

    await plugin.on_tool_error_callback(
        tool=_tool("load_memory"), tool_args={}, tool_context=None, error=ValueError("nope")
    )

    assert logged == [
        ("tool_used", {"tool": "load_memory", "status": "error", "error": "ValueError"})
    ]
