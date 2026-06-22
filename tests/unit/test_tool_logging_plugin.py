"""ToolLoggingPlugin is the single place tool calls are logged — one ``tool_used`` line per call."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from gaia.core.plugins import ToolLoggingPlugin
from gaia.tools.fs.base import current_project


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr("gaia.core.plugins.log_event", lambda a, **k: events.append((a, k)))
    return events


def _tool(name: str) -> Any:
    return SimpleNamespace(name=name)


def _ctx(agent: str | None = None, call_id: str | None = None) -> Any:
    return SimpleNamespace(agent_name=agent, function_call_id=call_id)


async def _run(plugin: ToolLoggingPlugin, tool: Any, args: Any, ctx: Any, result: Any) -> None:
    """Drive a full call: start (records time, no log) then finish (the one log line)."""
    await plugin.before_tool_callback(tool=tool, tool_args=args, tool_context=ctx)
    await plugin.after_tool_callback(tool=tool, tool_args=args, tool_context=ctx, result=result)


async def test_one_line_per_call_with_args_status_duration(
    logged: list[tuple[str, dict[str, Any]]],
) -> None:
    plugin = ToolLoggingPlugin()
    ctx = _ctx("gaia", call_id="fc-1")

    await _run(plugin, _tool("gh_search"), {"query": "adk", "per_page": 5}, ctx, {"status": "x"})

    # before_ logs nothing; exactly one tool_used line carries everything.
    assert [a for a, _ in logged] == ["tool_used"]
    fields = logged[0][1]
    assert fields["tool"] == "gh_search" and fields["agent"] == "gaia"
    assert fields["status"] == "x"
    assert fields["args"] == {"query": "adk", "per_page": 5}
    assert isinstance(fields["duration_ms"], int) and fields["duration_ms"] >= 0


async def test_status_defaults_to_ok_and_omits_unknown_agent(
    logged: list[tuple[str, dict[str, Any]]],
) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.after_tool_callback(
        tool=_tool("load_memory"), tool_args={}, tool_context=_ctx(), result={"memories": []}
    )

    name, fields = logged[0]
    assert name == "tool_used" and fields["status"] == "ok"
    assert "agent" not in fields  # omitted when unknown
    assert "duration_ms" not in fields  # no start recorded (no call_id) -> no duration


async def test_carries_project_during_a_soul_run(
    logged: list[tuple[str, dict[str, Any]]],
) -> None:
    plugin = ToolLoggingPlugin()
    token = current_project.set("plant-shop")
    try:
        await plugin.after_tool_callback(
            tool=_tool("fs_write"),
            tool_args={},
            tool_context=_ctx("frontend_developer"),
            result={"status": "success"},
        )
    finally:
        current_project.reset(token)

    assert logged[0][1]["project"] == "plant-shop"


async def test_no_project_field_for_root_agent(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()
    await plugin.after_tool_callback(
        tool=_tool("send_file"), tool_args={}, tool_context=_ctx("gaia"), result={"status": "ok"}
    )
    assert "project" not in logged[0][1]


async def test_args_are_sanitized(logged: list[tuple[str, dict[str, Any]]]) -> None:
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

    await plugin.after_tool_callback(
        tool=_tool("browser_type"),
        tool_args={"ref": "e2", "text": "hunter2-secret", "submit": True},
        tool_context=_ctx("gaia"),
        result={"status": "ok"},
    )
    await plugin.after_tool_callback(
        tool=_tool("remember"),
        tool_args={"fact": "the user's bank pin is 1234"},
        tool_context=_ctx("gaia"),
        result={"status": "ok"},
    )

    assert logged[0][1]["args"] == {"ref": "e2", "text": "[filtered]", "submit": True}
    assert "hunter2-secret" not in str(logged)
    assert logged[1][1]["args"] == {"fact": "[filtered]"}
    assert "1234" not in str(logged)


async def test_long_values_are_truncated(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.after_tool_callback(
        tool=_tool("exec"),
        tool_args={"command": "x" * 500, "background": False},
        tool_context=_ctx("gaia"),
        result={"status": "ok"},
    )

    args = logged[0][1]["args"]
    assert len(args["command"]) == 151 and args["command"].endswith("…")
    assert args["background"] is False  # scalars pass through untouched
    assert "x" * 500 not in str(logged)


async def test_non_dict_args_and_result_never_break(
    logged: list[tuple[str, dict[str, Any]]],
) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.after_tool_callback(
        tool=_tool("weird"),
        tool_args="not a dict",  # type: ignore[arg-type]
        tool_context=_ctx(),
        result="not a dict",  # type: ignore[arg-type]
    )

    assert logged[0] == ("tool_used", {"tool": "weird", "status": "ok"})  # no args, no agent


async def test_error_line_carries_the_command(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()
    ctx = _ctx("gaia", call_id="fc-err")

    await plugin.before_tool_callback(
        tool=_tool("exec"), tool_args={"command": "rm -rf /tmp/x"}, tool_context=ctx
    )
    await plugin.on_tool_error_callback(
        tool=_tool("exec"),
        tool_args={"command": "rm -rf /tmp/x"},
        tool_context=ctx,
        error=ValueError("nope"),
    )

    name, fields = logged[0]
    assert name == "tool_used" and fields["status"] == "error"
    assert fields["error"] == "ValueError"
    assert fields["args"] == {"command": "rm -rf /tmp/x"}  # the failing command is on the line
    assert "duration_ms" in fields


async def test_error_result_also_carries_args(logged: list[tuple[str, dict[str, Any]]]) -> None:
    # Most tool failures are a {"status": "error"} result (not an exception) — still show the args.
    plugin = ToolLoggingPlugin()

    await plugin.after_tool_callback(
        tool=_tool("fs_write"),
        tool_args={"path": "/nope"},
        tool_context=_ctx("gaia"),
        result={"status": "error", "error_message": "denied"},
    )

    fields = logged[0][1]
    assert fields["status"] == "error" and fields["args"] == {"path": "/nope"}


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
