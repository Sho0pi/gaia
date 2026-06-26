"""`gaia tools` — configure dispatch, reset, --all toggles, MCP add/remove."""

from __future__ import annotations

import pytest
import yaml
from typer.testing import CliRunner

from gaia import constants
from gaia.cli import app, setup
from gaia.cli._yamledit import set_config_value

runner = CliRunner()


def _yaml() -> dict:  # type: ignore[type-arg]
    return yaml.safe_load(constants.CONFIG_PATH.read_text()) or {}


def test_tools_help() -> None:
    assert runner.invoke(app, ["tools", "--help"]).exit_code == 0


def test_tools_configure_dispatches_to_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    # Picking web_search runs its flow (setup.search), via the lazy dispatch in tools._configure.
    monkeypatch.setattr("gaia.cli._select.select_manage", lambda *a, **k: (["web_search"], []))
    calls: list[str] = []
    monkeypatch.setattr(setup, "search", lambda ctx: calls.append("search"))

    result = runner.invoke(app, ["tools"])
    assert result.exit_code == 0, result.output
    assert calls == ["search"]


def test_tools_reset_browser_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    set_config_value(constants.CONFIG_PATH, "browser.backend", "native")  # non-default
    monkeypatch.setattr("gaia.cli._select.select_manage", lambda *a, **k: ([], ["browser"]))

    result = runner.invoke(app, ["tools"])
    assert result.exit_code == 0, result.output
    assert _yaml()["browser"]["backend"] == "mcp"  # reset to default


def test_tools_all_disables_a_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    # --all → select_many returns the enabled set minus web_fetch → web_fetch gets disabled.
    def fake_many(title, rows, *, selected=(), marked=()):  # type: ignore[no-untyped-def]
        return [t for t in selected if t != "web_fetch"]

    monkeypatch.setattr("gaia.cli._select.select_many", fake_many)
    result = runner.invoke(app, ["tools", "--all"])
    assert result.exit_code == 0, result.output
    assert _yaml()["tools"]["web_fetch"]["enabled"] is False


def test_tools_mcp_add_runs_add_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_manage(title, rows, *, marked=()):  # type: ignore[no-untyped-def]
        return (["__add__"], []) if title.startswith("Custom MCP") else (["mcp"], [])

    monkeypatch.setattr("gaia.cli._select.select_manage", fake_manage)
    calls: list[str] = []
    monkeypatch.setattr(setup, "mcp", lambda ctx: calls.append("add"))

    result = runner.invoke(app, ["tools"])
    assert result.exit_code == 0, result.output
    assert calls == ["add"]


def test_tools_mcp_remove_rewrites_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    set_config_value(
        constants.CONFIG_PATH,
        "mcp.servers",
        [{"name": "keep", "transport": "stdio"}, {"name": "drop", "transport": "stdio"}],
    )

    def fake_manage(title, rows, *, marked=()):  # type: ignore[no-untyped-def]
        return ([], ["drop"]) if title.startswith("Custom MCP") else (["mcp"], [])

    monkeypatch.setattr("gaia.cli._select.select_manage", fake_manage)
    result = runner.invoke(app, ["tools"])
    assert result.exit_code == 0, result.output
    names = [s["name"] for s in _yaml()["mcp"]["servers"]]
    assert names == ["keep"]  # drop removed
