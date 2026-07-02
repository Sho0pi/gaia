"""``gaia mcp`` command group: list, add, and remove external MCP servers.

Writes gaia.yaml's ``mcp.servers`` via the shared helpers in :mod:`gaia.mcp`. A change is picked up
when the toolsets rebuild: the in-chat ``/mcp`` command and the ``manage_mcp`` tool reset them live,
but from the CLI you restart a running daemon (``gaia restart``) to attach it.

Lazy-import rule (repo convention): only typer + stdlib (+ cli siblings) at module level.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from gaia.cli._console import console
from gaia.cli._options import state

app = typer.Typer(
    name="mcp", help="List, add, and remove external MCP servers.", no_args_is_help=True
)

NameArg = Annotated[str, typer.Argument(help="Server id.")]
TargetArg = Annotated[
    str, typer.Argument(help="A stdio launcher (uvx/npx/bunx) or a remote https:// url.")
]
RestArg = Annotated[list[str] | None, typer.Argument(help="Arguments for a stdio command.")]


def _cfg(ctx: typer.Context) -> Path:
    from gaia.config import get_settings

    return get_settings(state(ctx).env_file).config_path


@app.command("list")
def list_(ctx: typer.Context) -> None:
    """List the configured MCP servers."""
    from gaia import mcp as mcp_cfg

    servers = mcp_cfg.read_servers(_cfg(ctx))
    if not servers:
        console().print("No MCP servers wired. Add one with 'gaia mcp add', or ask Gaia in chat.")
        return
    for s in servers:
        off = "" if s.enabled else " [dim]· off[/]"
        ready = "" if mcp_cfg._runtime_available(s) else " [yellow]· not ready[/]"
        console().print(f"- [bold]{s.name}[/] ({s.transport}){off}{ready}")


@app.command()
def add(ctx: typer.Context, name: NameArg, target: TargetArg, rest: RestArg = None) -> None:
    """Add a server: stdio (a command + args) or remote (an https:// url).

    For a server needing a token or OAuth, ask Gaia in chat instead — it researches the server and
    wires the key. This command is the quick manual path for simple/keyless servers.
    """
    from gaia import mcp as mcp_cfg

    cfg = _cfg(ctx)
    try:
        if target.startswith(("http://", "https://")):
            server = mcp_cfg.add_server(cfg, name=name, transport="http", url=target)
        else:
            server = mcp_cfg.add_server(cfg, name=name, command=target, args=rest or [])
    except ValueError as exc:
        console().print(f"[red]{exc}[/]")
        raise typer.Exit(1) from None
    console().print(f"Added [bold]{server.name}[/]. Restart the daemon (gaia restart) to attach.")
    needs = mcp_cfg.env_refs(server)
    if needs:
        console().print(f"Set {', '.join(needs)} in ~/.gaia/.env first.")


@app.command()
def remove(ctx: typer.Context, name: NameArg) -> None:
    """Remove a server by name."""
    from gaia import mcp as mcp_cfg

    if not mcp_cfg.remove_server(_cfg(ctx), name):
        console().print(f"[red]No MCP server named {name!r}.[/]")
        raise typer.Exit(1)
    console().print(f"Removed [bold]{name}[/]. Restart a running daemon (gaia restart) to apply.")
