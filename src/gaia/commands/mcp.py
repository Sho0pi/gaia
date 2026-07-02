"""``/mcp`` — list, add, or remove external MCP servers (admin).

The human counterpart to the ``manage_mcp`` agent tool: both write ``mcp.servers`` via the shared
helpers in :mod:`gaia.mcp` and reset the toolsets singleton so a change is live next message. Rich
adds (auth, headers) are easier by just asking Gaia; this command is the quick manual surface.
"""

from __future__ import annotations

from gaia.commands.base import Command, CommandContext

_USAGE = (
    "Usage:\n"
    "  /mcp                          list servers\n"
    "  /mcp add <name> <command> [args…]   stdio (e.g. /mcp add time uvx mcp-server-time)\n"
    "  /mcp add <name> <https://url>       remote\n"
    "  /mcp remove <name>           remove one\n"
    "For anything needing auth/a token, just ask me — I'll research it and wire the key."
)


class MCPCommand(Command):
    name = "mcp"
    capability = "manage_users"

    async def run(self, ctx: CommandContext) -> str:
        from gaia import mcp as mcp_cfg

        cfg = ctx.gaia.settings.config_path
        parts = ctx.args.split()
        sub = parts[0].lower() if parts else "list"

        if sub == "list":
            servers = mcp_cfg.read_servers(cfg)
            if not servers:
                return "No MCP servers wired. Add one with /mcp add, or just ask me to."
            lines = []
            for s in servers:
                flags = ""
                if not s.enabled:
                    flags += " · off"
                if not mcp_cfg._runtime_available(s):
                    flags += " · ⚠ not ready"
                lines.append(f"- {s.name} [{s.transport}]{flags}")
            return "MCP servers:\n" + "\n".join(lines)

        if sub == "remove":
            if len(parts) < 2:
                return "Usage: /mcp remove <name>"
            name = parts[1]
            if not mcp_cfg.remove_server(cfg, name):
                return f"No MCP server named {name!r} (see /mcp)."
            ctx.gaia.container.mcp_toolsets.reset()
            return f"Removed {name!r}. Gone on the next message."

        if sub == "add":
            if len(parts) < 3:
                return _USAGE
            name, target, *rest = parts[1:]
            try:
                if target.startswith(("http://", "https://")):
                    server = mcp_cfg.add_server(cfg, name=name, transport="http", url=target)
                else:
                    server = mcp_cfg.add_server(cfg, name=name, command=target, args=rest)
            except ValueError as exc:
                return str(exc)
            ctx.gaia.container.mcp_toolsets.reset()
            needs = mcp_cfg.env_refs(server)
            msg = f"Added {server.name!r}. Live on the next message."
            if needs:
                msg += f" Put {', '.join(needs)} in ~/.gaia/.env."
            return msg

        return _USAGE
