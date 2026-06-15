"""Root Typer app: global flags, bare-invocation → chat, and the flat commands.

Lazy-import rule (repo convention): only typer + stdlib (+ cli siblings) at module
level. Everything heavy (``gaia.app`` → Gaia → ADK/connectors) is imported inside
command bodies so ``gaia --help`` never pays for it.

Exit codes: 0 ok · 1 runtime error · 2 usage (click default) · 3 daemon state.
"""

from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
from typing import Annotated

import typer

from gaia.cli._console import console, emit_json
from gaia.cli._options import CliState, state

app = typer.Typer(
    name="gaia",
    help="Gaia — an AI agent that spawns, stores, and fine-tunes task-specific subagents.",
    invoke_without_command=True,
    no_args_is_help=False,
)


def _version() -> str:
    try:
        return importlib.metadata.version("gaia")
    except importlib.metadata.PackageNotFoundError:  # running from an uninstalled tree
        from gaia import __version__

        return __version__


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"gaia {_version()}")
        raise typer.Exit()


def _chat(st: CliState) -> None:
    from gaia.app import run_cli

    run_cli(env_file=st.env_file)


@app.callback()
def root(
    ctx: typer.Context,
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="Secrets .env file (default: ~/.gaia/.env)."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Machine-readable JSON output on read commands.")
    ] = False,
    no_color: Annotated[
        bool, typer.Option("--no-color", help="Disable colored output (NO_COLOR also works).")
    ] = False,
    _version_flag: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Print the version and exit.",
        ),
    ] = False,
) -> None:
    """Bare invocation (no subcommand) opens the inline chat."""
    if no_color:
        os.environ["NO_COLOR"] = "1"  # rich honors it
    ctx.obj = CliState(env_file=env_file, json=json_output, no_color=no_color)
    if ctx.invoked_subcommand is None:
        _chat(ctx.obj)


@app.command()
def chat(ctx: typer.Context) -> None:
    """Chat with Gaia in the local terminal."""
    _chat(state(ctx))


@app.command()
def dev(
    ctx: typer.Context,
    host: Annotated[str, typer.Option(help="Dev web UI host.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Dev web UI port.")] = 8000,
) -> None:
    """Launch ADK's dev web UI on Gaia — inspect tool calls and LLM requests live."""
    from gaia.app import run_dev

    run_dev(env_file=state(ctx).env_file, host=host, port=port)


@app.command()
def msg(
    ctx: typer.Context,
    text: Annotated[str, typer.Argument(help="The message text to send.")],
    user: Annotated[
        str,
        typer.Option(
            "--user",
            help="The SENDER id (not a role), e.g. '972...@s.whatsapp.net' or '12345'.",
        ),
    ] = "local",
    channel: Annotated[
        str, typer.Option("--channel", help="Channel the sender is on (whatsapp/telegram/cli).")
    ] = "cli",
    name: Annotated[str, typer.Option("--name", help="Display name for a first-seen sender.")] = "",
) -> None:
    """Send one message through the multi-user dispatcher and print the reply.

    Sanity check for the access gate: an unknown sender on a guest-default channel is
    gated and prints nothing; a known user/admin gets a real model reply. Exits 1 when
    gated, 0 when a reply came back — so it scripts as a pass/fail check.
    """
    from gaia.app import send_message

    replies = send_message(channel, user, text, name=name, env_file=state(ctx).env_file)
    out = console()
    if not replies:
        out.print(f"[yellow]gated[/] — no reply for {channel}:{user} (guest or unknown sender)")
        raise typer.Exit(code=1)
    for reply in replies:
        out.print(reply)


@app.command()
def version(ctx: typer.Context) -> None:
    """Print the gaia version, Python version, and install location."""
    import platform

    import gaia

    info = {
        "name": "gaia",
        "version": _version(),
        "python": platform.python_version(),
        "location": str(Path(gaia.__file__).resolve().parent),
    }
    if state(ctx).json:
        emit_json(info)
    else:
        out = console()
        out.print(f"gaia {info['version']}")
        out.print(f"python {info['python']}")
        out.print(info["location"])
