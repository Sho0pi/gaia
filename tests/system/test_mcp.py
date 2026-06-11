"""System test: build a real MCP toolset from a reference server and list its tools.

Triple-gated so CI stays green without the optional bits: skip if the ``mcp`` package
isn't installed, skip if ``npx`` isn't on PATH, and skip unless ``GODPY_MCP_RUN_LIVE``
is set (npx downloads packages from the npm registry, which requires network access and
may be flaky in CI). Spawns ``@modelcontextprotocol/server-everything`` over stdio —
no godpy code.
"""

from __future__ import annotations

import os
import shutil

import pytest

pytest.importorskip("mcp", reason="needs the optional 'mcp' dep group")

from godpy.config.schema import MCPConfig, MCPServerConfig
from godpy.mcp import build_mcp_toolsets, close_mcp_toolsets

pytestmark = pytest.mark.system


@pytest.mark.skipif(
    not os.environ.get("GODPY_MCP_RUN_LIVE"),
    reason="spawns npx + downloads from npm; set GODPY_MCP_RUN_LIVE to run",
)
async def test_real_stdio_server_lists_tools() -> None:
    if shutil.which("npx") is None:
        pytest.skip("npx not on PATH (node runtime needed for the reference MCP server)")

    cfg = MCPConfig(
        servers=[
            MCPServerConfig(
                name="everything",
                command="npx",
                args=["-y", "@modelcontextprotocol/server-everything"],
            )
        ]
    )

    toolsets = build_mcp_toolsets(cfg)
    assert len(toolsets) == 1
    try:
        tools = await toolsets[0].get_tools()
        assert tools, "the reference server should expose at least one tool"
    finally:
        await close_mcp_toolsets(toolsets)  # no orphaned npx process
