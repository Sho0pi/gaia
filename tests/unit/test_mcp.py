"""Unit tests for the MCP toolset builder — config→params mapping + gated build."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from godpy.config.schema import BrowserConfig, MCPConfig, MCPServerConfig
from godpy.mcp import (
    build_mcp_toolsets,
    playwright_mcp_server,
    resolve_browser_backend,
    server_to_params,
)

pytest.importorskip("mcp", reason="needs the optional 'mcp' dep group")


# --- server_to_params (pure mapping) ----------------------------------------------


def test_stdio_params_merge_literal_and_passthrough_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "secret-from-env")
    server = MCPServerConfig(
        name="github",
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        env={"LOG": "debug"},
        env_passthrough=["GH_TOKEN", "MISSING_VAR"],
    )

    params = server_to_params(server)

    assert params.server_params.command == "npx"
    assert params.server_params.args == ["-y", "@modelcontextprotocol/server-github"]
    # literal env + passthrough copied from the process env; missing var simply skipped.
    assert params.server_params.env == {"LOG": "debug", "GH_TOKEN": "secret-from-env"}


def test_sse_and_http_params_carry_url_and_headers() -> None:
    sse = server_to_params(
        MCPServerConfig(name="s", transport="sse", url="https://x/sse", headers={"A": "1"})
    )
    http = server_to_params(MCPServerConfig(name="h", transport="http", url="https://x/mcp"))

    assert sse.url == "https://x/sse"
    assert http.url == "https://x/mcp"


def test_stdio_without_command_raises() -> None:
    with pytest.raises(ValueError, match="needs a 'command'"):
        server_to_params(MCPServerConfig(name="bad", transport="stdio"))


def test_remote_without_url_raises() -> None:
    with pytest.raises(ValueError, match="needs a 'url'"):
        server_to_params(MCPServerConfig(name="bad", transport="http"))


# --- build_mcp_toolsets (gated) ---------------------------------------------------


class _FakeToolset:
    """Captures the kwargs build_mcp_toolsets passes to McpToolset."""

    def __init__(self, *, connection_params: Any, tool_filter: Any, tool_name_prefix: Any) -> None:
        self.connection_params = connection_params
        self.tool_filter = tool_filter
        self.tool_name_prefix = tool_name_prefix


def _use_fake_toolset(monkeypatch: pytest.MonkeyPatch) -> None:
    # build_mcp_toolsets does `from google.adk.tools.mcp_tool import McpToolset` at call
    # time, so patching the attribute on that module injects the fake.
    monkeypatch.setattr("google.adk.tools.mcp_tool.McpToolset", _FakeToolset)


def test_no_servers_builds_nothing() -> None:
    assert build_mcp_toolsets(MCPConfig(servers=[])) == []


def test_disabled_servers_skipped() -> None:
    cfg = MCPConfig(servers=[MCPServerConfig(name="x", command="echo", enabled=False)])
    assert build_mcp_toolsets(cfg) == []


def test_missing_mcp_package_warns_and_returns_empty(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("godpy.mcp.importlib.util.find_spec", lambda name: None)
    cfg = MCPConfig(servers=[MCPServerConfig(name="x", command="echo")])

    with caplog.at_level(logging.WARNING, logger="godpy.mcp"):
        result = build_mcp_toolsets(cfg)

    assert result == []
    assert "mcp" in caplog.text.lower()


def test_stdio_command_not_on_path_skipped(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _use_fake_toolset(monkeypatch)
    monkeypatch.setattr("godpy.mcp.shutil.which", lambda cmd: None)
    cfg = MCPConfig(servers=[MCPServerConfig(name="ghost", command="nonesuch")])

    with caplog.at_level(logging.WARNING, logger="godpy.mcp"):
        result = build_mcp_toolsets(cfg)

    assert result == []
    assert "nonesuch" in caplog.text


def test_valid_server_builds_toolset_with_filter_and_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_fake_toolset(monkeypatch)
    cfg = MCPConfig(
        servers=[
            MCPServerConfig(
                name="fs",
                command="echo",  # on PATH → passes the runtime check
                tool_filter=["read_file", "list_dir"],
                tool_prefix="fs",
            )
        ]
    )

    toolsets = build_mcp_toolsets(cfg)

    assert len(toolsets) == 1
    ts = toolsets[0]
    assert ts.tool_filter == ["read_file", "list_dir"]
    assert ts.tool_name_prefix == "fs"
    assert ts.connection_params.server_params.command == "echo"


# --- browser backend: resolver + playwright-mcp synthesizer -----------------------


def test_resolve_backend_native_when_requested() -> None:
    assert resolve_browser_backend(BrowserConfig(backend="native")) == "native"


def test_resolve_backend_mcp_when_runtime_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("godpy.mcp.shutil.which", lambda cmd: "/usr/bin/bunx")
    assert resolve_browser_backend(BrowserConfig(backend="mcp")) == "mcp"


def test_resolve_backend_falls_back_when_runtime_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("godpy.mcp.shutil.which", lambda cmd: None)

    with caplog.at_level(logging.WARNING, logger="godpy.mcp"):
        backend = resolve_browser_backend(BrowserConfig(backend="mcp", runtime="bunx"))

    assert backend == "native"
    assert "bunx" in caplog.text


def test_playwright_mcp_server_maps_flags() -> None:
    server = playwright_mcp_server(
        BrowserConfig(
            runtime="bunx",
            headless=True,
            isolated=True,
            browser="chrome",
            allowed_origins=["https://a.com", "https://b.com"],
            tool_filter=["browser_navigate"],
        )
    )

    assert server.name == "playwright"
    assert server.command == "bunx"
    assert server.args[0] == "@playwright/mcp@latest"
    assert "--headless" in server.args
    assert "--isolated" in server.args
    assert server.args[server.args.index("--browser") + 1] == "chrome"
    assert server.args[server.args.index("--allowed-origins") + 1] == "https://a.com;https://b.com"
    assert server.tool_filter == ["browser_navigate"]
    assert server.tool_prefix is None  # names already browser_* — no double-prefix


def test_playwright_mcp_server_omits_flags_when_off() -> None:
    server = playwright_mcp_server(
        BrowserConfig(headless=False, isolated=False, allowed_origins=[])
    )

    assert "--headless" not in server.args
    assert "--isolated" not in server.args
    assert "--allowed-origins" not in server.args
