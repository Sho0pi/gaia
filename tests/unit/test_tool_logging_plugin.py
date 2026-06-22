"""ToolLoggingPlugin is the single place tool calls are logged — as a start+finish span."""

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


async def test_start_event_logs_tool_call_with_args(
    logged: list[tuple[str, dict[str, Any]]],
) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.before_tool_callback(
        tool=_tool("github_search_repositories"),
        tool_args={"query": "adk agents", "per_page": 5},
        tool_context=_ctx("gaia", call_id="fc-1"),
    )

    name, fields = logged[0]
    assert name == "tool_call"
    assert fields["tool"] == "github_search_repositories" and fields["agent"] == "gaia"
    assert fields["call_id"] == "fc-1"
    assert fields["args"] == {"query": "adk agents", "per_page": 5}


async def test_finish_event_logs_tool_used_with_status_and_duration(
    logged: list[tuple[str, dict[str, Any]]],
) -> None:
    plugin = ToolLoggingPlugin()
    ctx = _ctx("gaia", call_id="fc-2")

    await plugin.before_tool_callback(tool=_tool("some_tool"), tool_args={}, tool_context=ctx)
    await plugin.after_tool_callback(
        tool=_tool("some_tool"), tool_args={}, tool_context=ctx, result={"status": "success"}
    )

    assert [a for a, _ in logged] == ["tool_call", "tool_used"]
    finish = logged[1][1]
    assert finish["status"] == "success"
    assert finish["call_id"] == "fc-2"
    assert isinstance(finish["duration_ms"], int) and finish["duration_ms"] >= 0
    assert "args" not in finish  # args ride on the start event, not the finish


async def test_finish_status_defaults_to_ok_and_omits_unknown_agent(
    logged: list[tuple[str, dict[str, Any]]],
) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.after_tool_callback(
        tool=_tool("load_memory"), tool_args={}, tool_context=_ctx(), result={"memories": []}
    )

    name, fields = logged[0]
    assert name == "tool_used" and fields["status"] == "ok"
    assert "agent" not in fields  # omitted when unknown
    assert "duration_ms" not in fields  # no start was recorded (no call_id), so no span


async def test_base_fields_carry_project_during_a_soul_run(
    logged: list[tuple[str, dict[str, Any]]],
) -> None:
    plugin = ToolLoggingPlugin()
    token = current_project.set("plant-shop")
    try:
        await plugin.before_tool_callback(
            tool=_tool("fs_write"), tool_args={}, tool_context=_ctx("frontend_developer")
        )
    finally:
        current_project.reset(token)

    assert logged[0][1]["project"] == "plant-shop"


async def test_no_project_field_for_root_agent(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()
    # No soul run in progress (contextvar at its default ""): root tool calls carry no project.
    await plugin.before_tool_callback(
        tool=_tool("send_file"), tool_args={}, tool_context=_ctx("gaia")
    )
    assert "project" not in logged[0][1]


async def test_args_on_start_are_sanitized(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.before_tool_callback(
        tool=_tool("some_api_tool"),
        tool_args={
            "url": "https://api.example.com",
            "api_key": "sk-12345",
            "password": "hunter2",
            "authorization": "Bearer abc",
            "client_secret": "shhh",
        },
        tool_context=_ctx("gaia"),
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
    await plugin.before_tool_callback(
        tool=_tool("browser_type"),
        tool_args={"ref": "e2", "text": "hunter2-secret", "submit": True},
        tool_context=_ctx("gaia"),
    )
    # remember: the fact is private by definition.
    await plugin.before_tool_callback(
        tool=_tool("remember"),
        tool_args={"fact": "the user's bank pin is 1234"},
        tool_context=_ctx("gaia"),
    )

    assert logged[0][1]["args"] == {"ref": "e2", "text": "[filtered]", "submit": True}
    assert "hunter2-secret" not in str(logged)
    assert logged[1][1]["args"] == {"fact": "[filtered]"}
    assert "1234" not in str(logged)


async def test_long_values_are_truncated(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.before_tool_callback(
        tool=_tool("exec"),
        tool_args={"command": "x" * 500, "background": False},
        tool_context=_ctx("gaia"),
    )

    args = logged[0][1]["args"]
    assert len(args["command"]) == 151 and args["command"].endswith("…")
    assert args["background"] is False  # scalars pass through untouched
    assert "x" * 500 not in str(logged)


async def test_non_string_values_are_stringified(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.before_tool_callback(
        tool=_tool("fs_edit"),
        tool_args={"edits": [{"line": 3, "text": "new"}], "count": 2, "ratio": 0.5, "opt": None},
        tool_context=_ctx("gaia"),
    )

    args = logged[0][1]["args"]
    assert isinstance(args["edits"], str)  # nested structures become (truncated) text
    assert args["count"] == 2 and args["ratio"] == 0.5 and args["opt"] is None


async def test_non_dict_args_and_result_never_break(
    logged: list[tuple[str, dict[str, Any]]],
) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.before_tool_callback(
        tool=_tool("weird"),
        tool_args="not a dict",  # type: ignore[arg-type]
        tool_context=_ctx(),
    )
    await plugin.after_tool_callback(
        tool=_tool("weird"),
        tool_args="not a dict",  # type: ignore[arg-type]
        tool_context=_ctx(),
        result="not a dict",  # type: ignore[arg-type]
    )

    assert logged[0] == ("tool_call", {"tool": "weird"})  # no args, no agent
    assert logged[1][1]["status"] == "ok"


async def test_error_finish_logs_tool_used_error(
    logged: list[tuple[str, dict[str, Any]]],
) -> None:
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

    name, fields = logged[1]
    assert name == "tool_used" and fields["status"] == "error"
    assert fields["error"] == "ValueError" and fields["call_id"] == "fc-err"
    assert "duration_ms" in fields


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
