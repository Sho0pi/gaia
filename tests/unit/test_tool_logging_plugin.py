"""ToolLoggingPlugin is the single place tool calls are logged (one event per call)."""

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


def _ctx(agent: str | None = None) -> Any:
    return SimpleNamespace(agent_name=agent)


async def test_logs_every_tool_with_base_fields(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.after_tool_callback(
        tool=_tool("load_memory"), tool_args={}, tool_context=_ctx("god"), result={"memories": []}
    )

    # Built-ins (no policy entry) get tool/agent/status — status defaults to 'ok'.
    assert logged == [("tool_used", {"tool": "load_memory", "agent": "god", "status": "ok"})]


async def test_status_from_result_dict(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.after_tool_callback(
        tool=_tool("some_tool"), tool_args={}, tool_context=_ctx(), result={"status": "success"}
    )

    assert logged[0][1]["status"] == "success"
    assert "agent" not in logged[0][1]  # omitted when unknown


async def test_field_policy_adds_rich_fields(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.after_tool_callback(
        tool=_tool("web_search"),
        tool_args={"query": "  adk  "},
        tool_context=_ctx("god"),
        result={"status": "success", "results": [{}, {}]},
    )

    fields = logged[0][1]
    assert fields["query"] == "adk" and fields["results"] == 2


async def test_secret_args_are_never_logged(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()

    # browser_type: the typed text may be a password — must not appear.
    await plugin.after_tool_callback(
        tool=_tool("browser_type"),
        tool_args={"ref": "e2", "text": "hunter2-secret", "submit": True},
        tool_context=_ctx("god"),
        result={"status": "success"},
    )
    # exec: the command is truncated, never logged whole.
    await plugin.after_tool_callback(
        tool=_tool("exec"),
        tool_args={"command": "x" * 500},
        tool_context=_ctx("god"),
        result={"status": "success", "exit_code": 0},
    )

    type_fields = logged[0][1]
    assert type_fields["ref"] == "e2" and "text" not in type_fields
    assert "hunter2-secret" not in str(logged)

    exec_fields = logged[1][1]
    assert len(exec_fields["command"]) <= 120 and exec_fields["exit_code"] == 0


async def test_remember_logs_nothing_about_the_fact(
    logged: list[tuple[str, dict[str, Any]]],
) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.after_tool_callback(
        tool=_tool("remember"),
        tool_args={"fact": "the user's bank pin is 1234"},
        tool_context=_ctx("god"),
        result={"status": "success"},
    )

    assert "1234" not in str(logged)
    assert set(logged[0][1]) == {"tool", "agent", "status"}


async def test_non_dict_result_defaults_status_ok(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.after_tool_callback(
        tool=_tool("weird"),
        tool_args={},
        tool_context=_ctx(),
        result="not a dict",  # type: ignore[arg-type]
    )

    assert logged[0][1]["status"] == "ok"


async def test_raising_policy_falls_back_to_base(
    logged: list[tuple[str, dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A misbehaving policy must never break logging — fall back to base fields.
    def boom(_a: dict[str, Any], _r: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("policy bug")

    monkeypatch.setitem(
        __import__("godpy.god.plugins", fromlist=["_FIELD_POLICY"])._FIELD_POLICY,
        "web_search",
        boom,
    )
    plugin = ToolLoggingPlugin()

    await plugin.after_tool_callback(
        tool=_tool("web_search"), tool_args={}, tool_context=_ctx("god"), result={"status": "ok"}
    )

    assert logged[0][1] == {"tool": "web_search", "agent": "god", "status": "ok"}


async def test_logs_tool_error(logged: list[tuple[str, dict[str, Any]]]) -> None:
    plugin = ToolLoggingPlugin()

    await plugin.on_tool_error_callback(
        tool=_tool("exec"), tool_args={}, tool_context=_ctx("god"), error=ValueError("nope")
    )

    assert logged == [
        ("tool_used", {"tool": "exec", "agent": "god", "status": "error", "error": "ValueError"})
    ]


def test_no_tool_self_logs_anymore() -> None:
    """Guard: tools must not re-introduce per-tool logging (it lives in this plugin)."""
    import pathlib

    tools_dir = pathlib.Path("src/godpy/tools")
    offenders = [
        str(p)
        for p in tools_dir.rglob("*.py")
        if "log_event" in p.read_text() or "def done(" in p.read_text()
    ]
    assert offenders == [], f"tools must not self-log; centralize in ToolLoggingPlugin: {offenders}"
