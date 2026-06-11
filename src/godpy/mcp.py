"""Build ADK ``McpToolset``s from god.yaml's ``mcp`` config.

External MCP (Model Context Protocol) servers give God + its souls breadth — GitHub,
filesystem, databases, etc. — without bespoke godpy code. ADK ships the client
(:class:`google.adk.tools.mcp_tool.McpToolset`, a ``BaseToolset``); this module turns
each configured server into one, gated so a missing ``mcp`` dep or runtime degrades with
a warning instead of crashing (like fs_glob needing ``fd``).

The ADK/``mcp`` imports are deferred into :func:`build_mcp_toolsets` so importing godpy
(and constructing God in unit tests) needs neither. :func:`server_to_params` is the pure
config→connection-params mapping and is unit-testable on its own.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.tools.mcp_tool import (
        McpToolset,
        SseConnectionParams,
        StdioConnectionParams,
        StreamableHTTPConnectionParams,
    )

    from godpy.config.schema import MCPConfig, MCPServerConfig

logger = logging.getLogger(__name__)


def _stdio_env(server: MCPServerConfig) -> dict[str, str]:
    """The env a stdio server runs with: literal ``env`` + copied ``env_passthrough``.

    Secrets stay in the process environment and are copied by name — never written into
    god.yaml.
    """
    passed = {k: os.environ[k] for k in server.env_passthrough if k in os.environ}
    return {**server.env, **passed}


def server_to_params(
    server: MCPServerConfig,
) -> Union[StdioConnectionParams, SseConnectionParams, StreamableHTTPConnectionParams]:
    """Map a :class:`MCPServerConfig` to the matching ADK connection-params object.

    Imports ADK's mcp_tool lazily (it needs the ``mcp`` package). Raises ``ValueError``
    for a misconfigured server (e.g. stdio without a command, sse/http without a url).
    """
    from google.adk.tools.mcp_tool import (
        SseConnectionParams,
        StdioConnectionParams,
        StreamableHTTPConnectionParams,
    )

    if server.transport == "stdio":
        if not server.command:
            raise ValueError(f"mcp server {server.name!r}: stdio transport needs a 'command'")
        from mcp import StdioServerParameters

        return StdioConnectionParams(
            server_params=StdioServerParameters(
                command=server.command, args=server.args, env=_stdio_env(server)
            )
        )
    if not server.url:
        raise ValueError(f"mcp server {server.name!r}: {server.transport} transport needs a 'url'")
    if server.transport == "sse":
        return SseConnectionParams(url=server.url, headers=server.headers or None)
    return StreamableHTTPConnectionParams(url=server.url, headers=server.headers or None)


def _runtime_available(server: MCPServerConfig) -> bool:
    """True if the server's runtime is reachable (stdio command on PATH); others assumed up."""
    if server.transport == "stdio" and server.command:
        if shutil.which(server.command) is None:
            logger.warning(
                "mcp server %r disabled: command %r not found on PATH",
                server.name,
                server.command,
            )
            return False
    return True


def build_mcp_toolsets(config: MCPConfig) -> list[McpToolset]:
    """Build one ``McpToolset`` per enabled, reachable server in ``config``.

    Returns ``[]`` (no import) when nothing is configured. If servers are configured but
    the ``mcp`` package is absent, warns and returns ``[]`` — the rest of godpy keeps
    working. A single misconfigured server is skipped with a warning, not fatal.
    """
    servers = [s for s in config.servers if s.enabled]
    if not servers:
        return []
    if importlib.util.find_spec("mcp") is None:
        logger.warning(
            "MCP servers are configured but the 'mcp' package isn't installed — "
            "run: uv sync --group mcp"
        )
        return []

    from google.adk.tools.mcp_tool import McpToolset

    toolsets: list[McpToolset] = []
    for server in servers:
        if not _runtime_available(server):
            continue
        try:
            toolset = McpToolset(
                connection_params=server_to_params(server),
                tool_filter=server.tool_filter or None,
                tool_name_prefix=server.tool_prefix or None,
            )
        except Exception as exc:
            logger.warning("mcp server %r could not be attached: %s", server.name, exc)
            continue
        toolsets.append(toolset)
    return toolsets


async def close_mcp_toolsets(toolsets: list[McpToolset]) -> None:
    """Close each toolset (terminates stdio subprocesses). Best-effort, for shutdown."""
    for toolset in toolsets:
        close = getattr(toolset, "close", None)
        if close is None:
            continue
        try:
            await close()
        except Exception:  # pragma: no cover - shutdown best-effort
            logger.debug("mcp toolset close failed", exc_info=True)
