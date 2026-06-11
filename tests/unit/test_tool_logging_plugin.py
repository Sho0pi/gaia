"""ToolLoggingPlugin is the single place tool calls are logged (one event per call)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from gaia.core.plugins import ToolLoggingPlugin


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr("gaia.core.plugins.log_event", lambda a, **k: events.append((a, k)))
    return events


def _tool(name: str) -> Any:
    return SimpleNamespace(name=name)


def _ctx(agent: str | None = None) -> Any:
    return SimpleNamespace(agent_name=agent)


async def test_logs_every_tool_with_base_fields(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.after_tool_callback(
        tool=_tool("load_memory"), tool_args={}, tool_context=_ctx("gaia"), result={"memories": []}
    )

    # Empty args ⇒ tool/agent/status only — status defaults to 'ok'.
    assert logged == [("tool_used", {"tool": "load_memory", "agent": "gaia", "status": "ok"})]


async def test_status_from_result_dict(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.after_tool_callback(
        tool=_tool("some_tool"), tool_args={}, tool_context=_ctx(), result={"status": "success"}
    )

    assert logged[0][1]["status"] == "success"
    assert "agent" not in logged[0][1]  # omitted when unknown


async def test_args_logged_for_any_tool(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()

    # An MCP/never-seen tool: its args are still captured — no per-tool code needed.
    await plugin.after_tool_callback(
        tool=_tool("github_search_repositories"),
        tool_args={"query": "adk agents", "per_page": 5, "archived": False},
        tool_context=_ctx("gaia"),
        result={"status": "success"},
    )

    assert logged[0][1]["args"] == {"query": "adk agents", "per_page": 5, "archived": False}


async def test_sensitive_key_names_are_filtered(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.after_tool_callback(
        tool=_tool("some_api_tool"),
        tool_args={
            "url": "https://api.example.com",
            "api_key": "sk-12345",
            "password": "hunter2",
            "authorization": "Bearer abc",
            "client_secret": "shhh",
        },
        tool_context=_ctx("gaia"),
        result={"status": "success"},
    )

    args = logged[0][1]["args"]
    assert args["url"] == "https://api.example.com"
    assert args["api_key"] == "[filtered]"
    assert args["password"] == "[filtered]"
    assert args["authorization"] == "[filtered]"
    assert args["client_secret"] == "[filtered]"
    for secret in ("sk-12345", "hunter2", "Bearer abc", "shhh"):
        assert secret not in str(logged)


async def test_drop_list_filters_unnameable_secrets(
    logged: list[tuple[str, dict[str, Any]]],
) -> None:
    plugin = ToolLoggingPlugin()

    # browser_type: the typed text may be a password — must not appear.
    await plugin.after_tool_callback(
        tool=_tool("browser_type"),
        tool_args={"ref": "e2", "text": "hunter2-secret", "submit": True},
        tool_context=_ctx("gaia"),
        result={"status": "success"},
    )
    # remember: the fact is private by definition.
    await plugin.after_tool_callback(
        tool=_tool("remember"),
        tool_args={"fact": "the user's bank pin is 1234"},
        tool_context=_ctx("gaia"),
        result={"status": "success"},
    )

    type_args = logged[0][1]["args"]
    assert type_args == {"ref": "e2", "text": "[filtered]", "submit": True}
    assert "hunter2-secret" not in str(logged)
    assert logged[1][1]["args"] == {"fact": "[filtered]"}
    assert "1234" not in str(logged)


async def test_long_values_are_truncated(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.after_tool_callback(
        tool=_tool("exec"),
        tool_args={"command": "x" * 500, "background": False},
        tool_context=_ctx("gaia"),
        result={"status": "success"},
    )

    args = logged[0][1]["args"]
    assert len(args["command"]) == 151 and args["command"].endswith("…")
    assert args["background"] is False  # scalars pass through untouched
    assert "x" * 500 not in str(logged)


async def test_non_string_values_are_stringified(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.after_tool_callback(
        tool=_tool("fs_edit"),
        tool_args={"edits": [{"line": 3, "text": "new"}], "count": 2, "ratio": 0.5, "opt": None},
        tool_context=_ctx("gaia"),
        result={"status": "success"},
    )

    args = logged[0][1]["args"]
    assert isinstance(args["edits"], str)  # nested structures become (truncated) text
    assert args["count"] == 2 and args["ratio"] == 0.5 and args["opt"] is None


async def test_non_dict_result_and_args_never_break(
    logged: list[tuple[str, dict[str, Any]]],
) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.after_tool_callback(
        tool=_tool("weird"),
        tool_args="not a dict",  # type: ignore[arg-type]
        tool_context=_ctx(),
        result="not a dict",  # type: ignore[arg-type]
    )

    assert logged[0][1]["status"] == "ok"
    assert "args" not in logged[0][1]


async def test_logs_tool_error_with_args(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.on_tool_error_callback(
        tool=_tool("exec"),
        tool_args={"command": "rm -rf /tmp/x", "api_key": "sk-9"},
        tool_context=_ctx("gaia"),
        error=ValueError("nope"),
    )

    fields = logged[0][1]
    assert fields["tool"] == "exec" and fields["status"] == "error"
    assert fields["error"] == "ValueError"
    assert fields["args"] == {"command": "rm -rf /tmp/x", "api_key": "[filtered]"}


def test_no_tool_self_logs_anymore() -> None:
    """Guard: tools must not re-introduce per-tool logging (it lives in this plugin)."""
    import pathlib

    tools_dir = pathlib.Path("src/gaia/tools")
    offenders = [
        str(p)
        for p in tools_dir.rglob("*.py")
        if "log_event" in p.read_text() or "def done(" in p.read_text()
    ]
    assert offenders == [], f"tools must not self-log; centralize in ToolLoggingPlugin: {offenders}"
