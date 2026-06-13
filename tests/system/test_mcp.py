"""System test: build a real MCP toolset from a reference server and list its tools.

Triple-gated so CI stays green without the optional bits: skip if the ``mcp`` package
isn't installed, skip if ``bunx`` isn't on PATH, and skip unless ``GAIA_MCP_RUN_LIVE``
is set (bunx downloads packages from the registry, which requires network access and
may be flaky in CI). Spawns ``@modelcontextprotocol/server-everything`` over stdio —
no gaia code. bun is the repo's standard JS runtime (same as the browser backend).
"""

from __future__ import annotations

import os
import shutil

import pytest

pytest.importorskip("mcp", reason="needs the optional 'mcp' dep group")

from gaia.config.schema import BrowserConfig, MCPConfig, MCPServerConfig
from gaia.mcp import build_mcp_toolsets, close_mcp_toolsets, playwright_mcp_server

pytestmark = pytest.mark.system


@pytest.mark.skipif(
    not os.environ.get("GAIA_MCP_RUN_LIVE"),
    reason="spawns bunx + downloads the reference server; set GAIA_MCP_RUN_LIVE to run",
)
async def test_real_stdio_server_lists_tools() -> None:
    if shutil.which("bunx") is None:
        pytest.skip("bunx not on PATH (bun runtime needed for the reference MCP server)")

    cfg = MCPConfig(
        servers=[
            MCPServerConfig(
                name="everything",
                command="bunx",
                args=["@modelcontextprotocol/server-everything"],
            )
        ]
    )

    toolsets = build_mcp_toolsets(cfg)
    assert len(toolsets) == 1
    try:
        tools = await toolsets[0].get_tools()
        assert tools, "the reference server should expose at least one tool"
    finally:
        await close_mcp_toolsets(toolsets)  # no orphaned bunx process


@pytest.mark.skipif(
    not os.environ.get("GAIA_BROWSER_MCP_RUN_LIVE"),
    reason="spawns bunx + downloads @playwright/mcp; set GAIA_BROWSER_MCP_RUN_LIVE to run",
)
async def test_playwright_mcp_lists_tools() -> None:
    if shutil.which("bunx") is None:
        pytest.skip("bunx not on PATH (bun runtime needed for playwright-mcp)")

    cfg = MCPConfig(servers=[playwright_mcp_server(BrowserConfig())])

    toolsets = build_mcp_toolsets(cfg)
    assert len(toolsets) == 1
    try:
        tools = await toolsets[0].get_tools()
        assert tools, "playwright-mcp should expose at least one tool"
    finally:
        await close_mcp_toolsets(toolsets)  # no orphaned bunx process
