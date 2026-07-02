"""The ``manage_mcp`` tool: let an admin add/list/remove external MCP servers by chat.

Root-only and bound to the live :class:`~gaia.core.agent.Gaia` (like ``manage_permission``) because
it writes ``mcp.servers`` in gaia.yaml (via the shared helpers in :mod:`gaia.mcp`). Self-gated on
``manage_users`` — attached to every root agent, but only an admin can use it. After a change it
resets the ``mcp_toolsets`` singleton so the next turn's rebuilt agent attaches the new server (no
daemon restart). It never handles raw secrets: it records env-var *names* (in ``env_passthrough`` or
as ``${VAR}`` in a header); the operator puts the value in ``~/.gaia/.env``. Tools never raise to
the model; failures come back as an error dict.
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
        headers: dict[str, str] | None = None,
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
           `npx -y <package>` / `bunx <package>`. A remote one exposes a url ("http"/"sse") and
           usually needs a token (a Bearer header) or OAuth.
        2. CONFIRM before adding — an MCP server is third-party code that runs on the user's box.
           Show exactly what you'll add: the name, the package/command or url, and the source, and
           proceed only after they say yes (use ask_user). If several plausible servers exist, ask
           which one rather than guessing.
        3. action="add": stdio → command + args; remote → transport + url. For a remote server that
           needs a token, pass headers={"Authorization": "Bearer ${TICKTICK_TOKEN}"} — reference the
           key by ${NAME}, never inline the secret. (For stdio, use env_passthrough for the name.)
           A remote server that only supports interactive OAuth login (no token option) isn't wired
           yet — tell the user that.
        4. KEYS — if it needs a key, call save_secret(env_var="TICKTICK_TOKEN") to collect it from
           the user; it stores the value in ~/.gaia/.env and the live env, and you never see it,
           and it's usable immediately (no restart). Then reference it as ${TICKTICK_TOKEN} in the
           header above (or env_passthrough for stdio). Never ask for the raw secret in plain chat.
        The server attaches on the user's NEXT message (no restart). action="list" shows what's
        wired; action="remove" drops one by name.

        Args:
            action: "add", "list", or "remove".
            name: server id (add/remove) — short, unique, used as the tool-name prefix in logs.
            transport: "stdio" (local command, default), "http", or "sse" (remote url).
            command: stdio only — the launcher, e.g. "uvx", "npx", "bunx".
            args: stdio only — arguments, e.g. ["-y", "some-mcp-server"].
            url: http/sse only — the server URL.
            env_passthrough: env var NAMES to pass from the daemon env to a stdio server (secrets).
            env: literal NON-secret env vars for a stdio server.
            headers: http/sse only — request headers; use ${VAR} for a secret (e.g. a Bearer token).
            tool_filter: only load these tool names from the server (empty = all).
            tool_prefix: prefix the server's tool names to avoid collisions.
        """
        from gaia import mcp as mcp_cfg
        from gaia.acl import MANAGE_USERS, can

        caller_id = getattr(tool_context, "user_id", None)
        caller = gaia.users.get(caller_id) if caller_id else None
        # caller is None only off the dispatch path (cron/cli/tests) — trusted there.
        if caller is not None and not can(caller, MANAGE_USERS, gaia.config):
            return err("only an admin can manage MCP servers")

        cfg_path = gaia.settings.config_path
        act = action.strip().lower()

        if act == "list":
            return ok(
                servers=[
                    {
                        "name": s.name,
                        "transport": s.transport,
                        "enabled": s.enabled,
                        "ready": mcp_cfg._runtime_available(s),
                    }
                    for s in mcp_cfg.read_servers(cfg_path)
                ]
            )

        if act == "remove":
            nm = name.strip()
            if not nm:
                return err("name is required to remove a server")
            if not mcp_cfg.remove_server(cfg_path, nm):
                return err(f"no MCP server named {nm!r} (try action='list')")
            gaia.container.mcp_toolsets.reset()
            return ok(removed=nm, message=f"removed {nm!r}; gone on your next message")

        if act != "add":
            return err("action must be 'add', 'list', or 'remove'")

        try:
            server = mcp_cfg.add_server(
                cfg_path,
                name=name.strip(),
                transport=transport.strip().lower() or "stdio",
                command=command.strip() or None,
                args=args,
                url=url.strip() or None,
                env=env,
                env_passthrough=env_passthrough,
                headers=headers,
                tool_filter=tool_filter,
                tool_prefix=tool_prefix.strip() or None,
            )
        except ValueError as exc:
            return err(str(exc))
        except Exception as exc:  # pydantic ValidationError et al. — never raise to the model
            return err(f"invalid MCP server config: {exc}")

        gaia.container.mcp_toolsets.reset()  # next agent build rebuilds toolsets from new config
        # ponytail: the old toolsets' subprocesses linger until shutdown (their lifecycle closer
        # still fires); explicit close-on-reset is a follow-up if it leaks.
        needs = mcp_cfg.env_refs(server)
        message = f"added {server.name!r}; it attaches on your next message."
        if needs:
            message += f" First put {', '.join(needs)} in ~/.gaia/.env."
        return ok(added=server.name, needs_env=needs, message=message)

    return manage_mcp
