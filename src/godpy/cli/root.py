"""Root Typer app: global flags, bare-invocation → chat, and the flat commands.

Lazy-import rule (repo convention): only typer + stdlib (+ cli siblings) at module
level. Everything heavy (``godpy.app`` → God → ADK/connectors) is imported inside
command bodies so ``godpy --help`` never pays for it.

Exit codes: 0 ok · 1 runtime error · 2 usage (click default).
"""

from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
from typing import Annotated

import typer

from godpy.cli._console import console, emit_json
from godpy.cli._options import CliState, state

app = typer.Typer(
    name="godpy",
    help="God — an AI agent that spawns, stores, and fine-tunes task-specific subagents.",
    invoke_without_command=True,
    no_args_is_help=False,
)


def _version() -> str:
    try:
        return importlib.metadata.version("godpy")
    except importlib.metadata.PackageNotFoundError:  # running from an uninstalled tree
        from godpy import __version__

        return __version__


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"godpy {_version()}")
        raise typer.Exit()


def _chat(st: CliState) -> None:
    from godpy.app import run_cli

    run_cli(env_file=st.env_file)


@app.callback()
def root(
    ctx: typer.Context,
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="Secrets .env file (default: ~/.godpy/.env)."),
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
    """Bare invocation (no subcommand) opens the chat TUI."""
    if no_color:
        os.environ["NO_COLOR"] = "1"  # rich and textual both honor it
    ctx.obj = CliState(env_file=env_file, json=json_output, no_color=no_color)
    if ctx.invoked_subcommand is None:
        _chat(ctx.obj)


@app.command()
def chat(ctx: typer.Context) -> None:
    """Chat with God in the local terminal TUI."""
    _chat(state(ctx))


@app.command()
def serve(ctx: typer.Context) -> None:
    """Run the connectors enabled in god.yaml in the foreground."""
    from godpy.app import run

    run(env_file=state(ctx).env_file)


@app.command()
def dev(
    ctx: typer.Context,
    host: Annotated[str, typer.Option(help="Dev web UI host.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Dev web UI port.")] = 8000,
) -> None:
    """Launch ADK's dev web UI on God — inspect tool calls and LLM requests live."""
    from godpy.app import run_dev

    run_dev(env_file=state(ctx).env_file, host=host, port=port)


@app.command()
def version(ctx: typer.Context) -> None:
    """Print the godpy version, Python version, and install location."""
    import platform

    import godpy

    info = {
        "name": "godpy",
        "version": _version(),
        "python": platform.python_version(),
        "location": str(Path(godpy.__file__).resolve().parent),
    }
    if state(ctx).json:
        emit_json(info)
    else:
        out = console()
        out.print(f"godpy {info['version']}")
        out.print(f"python {info['python']}")
        out.print(info["location"])
