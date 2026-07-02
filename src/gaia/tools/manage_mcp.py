"""The ``manage_mcp`` tool: let an admin add/list/remove external MCP servers by chat.

Root-only and bound to the live :class:`~gaia.core.agent.Gaia` (like ``manage_permission``) because
it writes ``mcp.servers`` in gaia.yaml. Self-gated on ``manage_users`` — attached to every root
agent, but only an admin can use it. After a change it resets the ``mcp_toolsets`` singleton so next
turn's rebuilt agent attaches the new server (no daemon restart). It never handles secrets: it
records env-var *names* in ``env_passthrough``; the operator puts the value in ``.env``.
Tools never raise to the model; failures come back as an error dict.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from google.adk.tools.tool_context import ToolContext

from gaia.tools._helpers import err, ok

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia

#: Tool id / ADK tool name (matches the closure name).
NAME = "manage_mcp"


def _write_servers(cfg_path: Any, servers: list[dict[str, Any]]) -> None:
    """Persist the full ``mcp.servers`` list to gaia.yaml (comment-preserving)."""
    from gaia.cli._yamledit import set_config_value

    set_config_value(cfg_path, "mcp.servers", servers)


def make_manage_mcp(gaia: Gaia) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the root-only ``manage_mcp`` tool bound to ``gaia``."""

    async def manage_mcp(
        action: str,
        name: str = "",
        transport: str = "stdio",
        command: str = "",
        args: list[str] | None = None,
        url: str = "",
        env_passthrough: list[str] | None = None,
        env: dict[str, str] | None = None,
        tool_filter: list[str] | None = None,
        tool_prefix: str = "",
        *,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Add, list, or remove an external MCP server that gives Gaia new tools (admin only).

        Adding an integration (e.g. "add ticktick mcp to update todos") is research-confirm-wire:
        1. RESEARCH — web_search for the server. There are usually MANY (forks, look-alikes); PREFER
           the trusted one: the vendor's own, the official modelcontextprotocol/servers repo, or the
           clearly most-starred, actively-maintained package. Find how it launches and what auth it
           needs. A stdio server runs a local command: Python via `uvx <package>`, Node via
           `npx -y <package>` / `bunx <package>`; a remote one exposes a url ("http"/"sse").
           When the service needs an API key, strongly PREFER a stdio package that takes the token
           via env_passthrough — the .env key flow (step 4) works for it. A remote http/sse endpoint
           almost always needs auth (a Bearer header or OAuth) which isn't wired yet, so a bare url
           will just fail with 401; only add a remote server if it's genuinely public/keyless.
        2. CONFIRM before adding — an MCP server is third-party code that runs on the user's box.
           Show exactly what you'll add: the name, the package/command, and the source URL
           (GitHub/registry), and proceed only after they say yes (use ask_user). If several
           plausible servers exist, ask which one rather than guessing.
        3. action="add": stdio → command + args; remote → transport + url. Put any API-key env var
           NAMES in env_passthrough (names only, e.g. ["TICKTICK_TOKEN"] — never the secret).
        4. If it needs a key, tell the user to create it and add it to ~/.gaia/.env as that variable
           (e.g. TICKTICK_TOKEN=...). Do NOT ask for the raw secret here — it must not pass through
           you; the server reads it from the daemon env at launch.
        The server attaches on the user's NEXT message (no restart). action="list" shows what's
        wired; action="remove" drops one by name.

        Args:
            action: "add", "list", or "remove".
            name: server id (add/remove) — short, unique, used as the tool-name prefix in logs.
            transport: "stdio" (local command, default), "http", or "sse" (remote url).
            command: stdio only — the launcher, e.g. "uvx", "npx", "bunx".
            args: stdio only — arguments, e.g. ["-y", "some-mcp-server"].
            url: http/sse only — the server URL.
            env_passthrough: env var NAMES to pass from the daemon env to the server (for secrets).
            env: literal NON-secret env vars for the server.
            tool_filter: only load these tool names from the server (empty = all).
            tool_prefix: prefix the server's tool names to avoid collisions.
        """
        from gaia.acl import MANAGE_USERS, can
        from gaia.config import ConfigSupplier
        from gaia.config.schema import MCPServerConfig

        caller_id = getattr(tool_context, "user_id", None)
        caller = gaia.users.get(caller_id) if caller_id else None
        # caller is None only off the dispatch path (cron/cli/tests) — trusted there.
        if caller is not None and not can(caller, MANAGE_USERS, gaia.config):
            return err("only an admin can manage MCP servers")

        cfg_path = gaia.settings.config_path
        current = list(ConfigSupplier(cfg_path).current.mcp.servers)
        act = action.strip().lower()

        if act == "list":
            from gaia.mcp import _runtime_available

            return ok(
                servers=[
                    {
                        "name": s.name,
                        "transport": s.transport,
                        "enabled": s.enabled,
                        "ready": _runtime_available(s),
                    }
                    for s in current
                ]
            )

        if act == "remove":
            nm = name.strip()
            if not nm:
                return err("name is required to remove a server")
            keep = [s for s in current if s.name != nm]
            if len(keep) == len(current):
                return err(f"no MCP server named {nm!r} (try action='list')")
            _write_servers(cfg_path, [s.model_dump(exclude_defaults=True) for s in keep])
            gaia.container.mcp_toolsets.reset()
            return ok(removed=nm, message=f"removed {nm!r}; gone on your next message")

        if act != "add":
            return err("action must be 'add', 'list', or 'remove'")

        nm = name.strip()
        if not nm:
            return err("name is required to add a server")
        if any(s.name == nm for s in current):
            return err(f"an MCP server named {nm!r} already exists (remove it first)")
        t = transport.strip().lower() or "stdio"
        if t == "stdio" and not command.strip():
            return err("stdio transport needs a command (e.g. 'uvx', 'npx', 'bunx')")
        if t in ("http", "sse") and not url.strip():
            return err(f"{t} transport needs a url")

        server: dict[str, Any] = {
            "name": nm,
            "transport": t,
            "command": command.strip() or None,
            "args": args or [],
            "url": url.strip() or None,
            "env": env or {},
            "env_passthrough": env_passthrough or [],
            "tool_filter": tool_filter or [],
            "tool_prefix": tool_prefix.strip() or None,
        }
        try:
            validated = MCPServerConfig(**server)
        except Exception as exc:  # pydantic ValidationError et al. — never raise to the model
            return err(f"invalid MCP server config: {exc}")

        new_list = [
            *[s.model_dump(exclude_defaults=True) for s in current],
            validated.model_dump(exclude_defaults=True),
        ]
        _write_servers(cfg_path, new_list)
        gaia.container.mcp_toolsets.reset()  # next agent build rebuilds toolsets from new config
        # ponytail: the old toolsets' subprocesses linger until shutdown (their lifecycle closer
        # still fires); explicit close-on-reset is a follow-up if it leaks.
        needs = validated.env_passthrough
        message = f"added {nm!r}; it attaches on your next message."
        if needs:
            message += f" First put {', '.join(needs)} in ~/.gaia/.env."
        return ok(added=nm, needs_env=needs, message=message)

    return manage_mcp
