"""Build ADK ``McpToolset``s from gaia.yaml's ``mcp`` config.

External MCP (Model Context Protocol) servers give Gaia + its souls breadth — GitHub,
filesystem, databases, etc. — without bespoke gaia code. ADK ships the client
(:class:`google.adk.tools.mcp_tool.McpToolset`, a ``BaseToolset``); this module turns
each configured server into one, gated so a missing ``mcp`` dep or runtime degrades with
a warning instead of crashing (like fs_glob needing ``fd``).

The ADK/``mcp`` imports are deferred into :func:`build_mcp_toolsets` so importing gaia
(and constructing Gaia in unit tests) needs neither. :func:`server_to_params` is the pure
config→connection-params mapping and is unit-testable on its own.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from gaia import constants
from gaia.config.schema import MCPServerConfig

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.tools.mcp_tool import (
        McpToolset,
        SseConnectionParams,
        StdioConnectionParams,
        StreamableHTTPConnectionParams,
    )

    from gaia.config.schema import BrowserConfig, MCPConfig

logger = logging.getLogger(__name__)


def _resolve_runtime(name: str) -> str | None:
    """Absolute path to runtime ``name`` — PATH first, then the installers' well-known dirs.

    The daemon (and the launchd/systemd service) don't inherit the shell rc PATH, so a bun
    installed at ``~/.bun/bin`` is invisible to a bare ``shutil.which("bunx")`` — the browser
    then silently falls back to native (#296). Check the common no-sudo install locations so
    gaia finds the runtime however it was launched. Already-absolute names pass through.
    """
    found = shutil.which(name)
    if found:
        return found
    home = Path.home()
    for directory in (home / ".bun" / "bin", home / ".local" / "bin", home / ".cargo" / "bin"):
        candidate = directory / name
        if candidate.is_file():
            return str(candidate)
    return None


def _stdio_env(server: MCPServerConfig) -> dict[str, str]:
    """The env a stdio server runs with: literal ``env`` + copied ``env_passthrough``.

    Secrets stay in the process environment and are copied by name — never written into
    gaia.yaml.
    """
    passed = {k: os.environ[k] for k in server.env_passthrough if k in os.environ}
    return {**server.env, **passed}


_ENV_REF = re.compile(r"\$\{(\w+)\}")


def _expand_env(value: str) -> str:
    """Replace ``${VAR}`` in a string with its env value (empty if unset).

    Lets a remote server carry ``Authorization: Bearer ${TICKTICK_TOKEN}`` in gaia.yaml while the
    actual token lives in ``~/.gaia/.env`` — the secret never touches the config file.
    """
    return _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), value)


def _headers(server: MCPServerConfig) -> dict[str, str] | None:
    """Request headers for an sse/http server, with ``${VAR}`` expanded from the env."""
    if not server.headers:
        return None
    return {k: _expand_env(v) for k, v in server.headers.items()}


# --- config CRUD (shared by the manage_mcp tool, the /mcp command, and `gaia mcp`) ------------


def read_servers(cfg_path: Path) -> list[MCPServerConfig]:
    """The configured MCP servers from gaia.yaml (validated)."""
    from gaia.config import ConfigSupplier

    return list(ConfigSupplier(cfg_path).current.mcp.servers)


def _write_servers(cfg_path: Path, servers: list[dict[str, object]]) -> None:
    from gaia.cli._yamledit import set_config_value

    set_config_value(cfg_path, "mcp.servers", servers)


def add_server(
    cfg_path: Path,
    *,
    name: str,
    transport: str = "stdio",
    command: str | None = None,
    args: list[str] | None = None,
    url: str | None = None,
    env: dict[str, str] | None = None,
    env_passthrough: list[str] | None = None,
    headers: dict[str, str] | None = None,
    tool_filter: list[str] | None = None,
    tool_prefix: str | None = None,
) -> MCPServerConfig:
    """Validate + append an MCP server to gaia.yaml. Raises ValueError on bad config / dup name."""
    server = MCPServerConfig(
        name=name,
        transport=transport,  # type: ignore[arg-type]  # validated by the model
        command=command or None,
        args=args or [],
        url=url or None,
        env=env or {},
        env_passthrough=env_passthrough or [],
        headers=headers or {},
        tool_filter=tool_filter or [],
        tool_prefix=tool_prefix or None,
    )
    if server.transport == "stdio" and not server.command:
        raise ValueError("stdio transport needs a command (e.g. 'uvx', 'npx', 'bunx')")
    if server.transport in ("http", "sse") and not server.url:
        raise ValueError(f"{server.transport} transport needs a url")
    current = read_servers(cfg_path)
    if any(s.name == server.name for s in current):
        raise ValueError(f"an MCP server named {server.name!r} already exists")
    dumps = [s.model_dump(exclude_defaults=True) for s in current]
    dumps.append(server.model_dump(exclude_defaults=True))
    _write_servers(cfg_path, dumps)
    return server


def env_refs(server: MCPServerConfig) -> list[str]:
    """Env var names a server needs: ``env_passthrough`` + ``${VAR}`` refs in its headers.

    Used to tell the user which keys to put in ``~/.gaia/.env`` for the server to authenticate.
    """
    names = list(server.env_passthrough)
    for value in server.headers.values():
        names += _ENV_REF.findall(value)
    return list(dict.fromkeys(names))  # de-dup, keep order


def remove_server(cfg_path: Path, name: str) -> bool:
    """Drop the named server from gaia.yaml. Returns False if there was no such server."""
    current = read_servers(cfg_path)
    keep = [s for s in current if s.name != name]
    if len(keep) == len(current):
        return False
    _write_servers(cfg_path, [s.model_dump(exclude_defaults=True) for s in keep])
    return True


def server_to_params(
    server: MCPServerConfig,
) -> StdioConnectionParams | SseConnectionParams | StreamableHTTPConnectionParams:
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

        # Resolve to an absolute path so the launch doesn't depend on the daemon/service PATH
        # (bunx lives in ~/.bun/bin, which isn't on a service's PATH) — #296.
        command = _resolve_runtime(server.command) or server.command
        return StdioConnectionParams(
            server_params=StdioServerParameters(
                command=command, args=server.args, env=_stdio_env(server), cwd=server.cwd
            )
        )
    if not server.url:
        raise ValueError(f"mcp server {server.name!r}: {server.transport} transport needs a 'url'")
    if server.transport == "sse":
        return SseConnectionParams(url=server.url, headers=_headers(server))
    return StreamableHTTPConnectionParams(url=server.url, headers=_headers(server))


def _runtime_available(server: MCPServerConfig) -> bool:
    """True if the server's runtime is reachable (stdio command on PATH); others assumed up."""
    if server.transport == "stdio" and server.command:
        if _resolve_runtime(server.command) is None:
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
    the ``mcp`` package is absent, warns and returns ``[]`` — the rest of gaia keeps
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


# --- Browser backend: Microsoft playwright-mcp as a synthesized MCP server ---------------


def resolve_browser_backend(config: BrowserConfig) -> Literal["native", "mcp"]:
    """The effective browser backend: ``mcp`` only if requested AND its runtime is on PATH.

    Single source of truth consulted by both the tool registry (native-tool gating) and
    :meth:`Gaia.mcp_toolsets` (playwright-mcp attach), so the two never double-register the
    browser. When ``backend == "mcp"`` but the runtime is missing, warns once and falls
    back to ``native`` — the same graceful degradation as the fd/rg/playwright gates.
    """
    if config.backend != "mcp":
        return "native"
    if _resolve_runtime(config.runtime) is None:
        logger.warning(
            "browser backend 'mcp' requested but %r is not on PATH; falling back to the "
            "native browser tools (install bun: https://bun.sh)",
            config.runtime,
        )
        return "native"
    return "mcp"


def browser_output_dir() -> Path:
    """Where the shared playwright-mcp server writes screenshots/PDFs.

    playwright-mcp is ONE process for all agents, so its output can't be per-agent like
    the native tools — files land in the root ``gaia`` agent's workspace under
    ``.gaia/agents/`` (matching ``sandbox_for(AGENTS_DIR, "gaia")``). Without this,
    playwright-mcp defaults to a ``.playwright-mcp`` dir in the current working
    directory (i.e. the project tree).

    Per-agent isolation (shared browser/cookies/output) is tracked in issue #94.
    """
    return constants.AGENTS_DIR / "gaia" / "workspace"


def playwright_mcp_server(
    config: BrowserConfig, *, output_dir: Path | None = None
) -> MCPServerConfig:
    """Synthesize the :class:`MCPServerConfig` for Microsoft's playwright-mcp.

    Runs over stdio with the configured runtime (``bunx``). The server's **cwd** is pinned
    to the gaia workspace: ``browser_take_screenshot`` writes its file relative to the
    process cwd (it ignores ``--output-dir`` for the screenshot ``filename``), so without
    this the PNG lands in gaia's own cwd and the connector can't find it to send. With cwd
    set, the screenshot's ``./<name>.png`` resolves under the workspace where
    :func:`gaia.core.screenshots.media_for_screenshots` looks. ``--output-dir`` still pins
    session/trace files. No ``tool_prefix``: the server's tools are already named
    ``browser_*``. The caller attaches this only when ``resolve_browser_backend`` is mcp.
    """
    out = output_dir or browser_output_dir()
    out.mkdir(parents=True, exist_ok=True)  # the server's cwd must exist at launch
    args = [config.package, "--browser", config.browser, "--output-dir", str(out)]
    if config.headless:
        args.append("--headless")
    if config.isolated:
        args.append("--isolated")
    if config.allowed_origins:
        args += ["--allowed-origins", ";".join(config.allowed_origins)]
    return MCPServerConfig(
        name="playwright",
        transport="stdio",
        command=config.runtime,
        args=args,
        cwd=str(out),
        tool_filter=config.tool_filter,
    )
