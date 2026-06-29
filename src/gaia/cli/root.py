"""Root Typer app: global flags, bare-invocation → chat, and the flat commands.

Lazy-import rule (repo convention): only typer + stdlib (+ cli siblings) at module
level. Everything heavy (``gaia.app`` → Gaia → ADK/connectors) is imported inside
command bodies so ``gaia --help`` never pays for it.

Exit codes: 0 ok · 1 runtime error · 2 usage (click default) · 3 daemon state.
"""

from __future__ import annotations

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


def _version_callback(value: bool) -> None:
    if value:
        from gaia import version

        typer.echo(f"gaia {version()}")
        raise typer.Exit()


def _chat(st: CliState) -> None:
    from gaia.app import run_cli

    run_cli(env_file=st.env_file)


# Argument/option types named once so the command signatures below stay readable.
EnvFileOpt = Annotated[
    Path | None, typer.Option("--env-file", help="Secrets .env file (default: ~/.gaia/.env).")
]
JsonOpt = Annotated[
    bool, typer.Option("--json", help="Machine-readable JSON output on read commands.")
]
NoColorOpt = Annotated[
    bool, typer.Option("--no-color", help="Disable colored output (NO_COLOR also works).")
]
VersionOpt = Annotated[
    bool,
    typer.Option(
        "--version", callback=_version_callback, is_eager=True, help="Print the version and exit."
    ),
]
HostOpt = Annotated[str, typer.Option(help="Dev web UI host.")]
PortOpt = Annotated[int, typer.Option(help="Dev web UI port.")]
TextArg = Annotated[str, typer.Argument(help="The message text to send.")]
UserOpt = Annotated[
    str,
    typer.Option(
        "--user", help="The SENDER id (not a role), e.g. '972...@s.whatsapp.net' or '12345'."
    ),
]
ChannelOpt = Annotated[
    str, typer.Option("--channel", help="Channel the sender is on (whatsapp/telegram/cli).")
]
NameOpt = Annotated[str, typer.Option("--name", help="Display name for a first-seen sender.")]


@app.callback()
def root(
    ctx: typer.Context,
    env_file: EnvFileOpt = None,
    json_output: JsonOpt = False,
    no_color: NoColorOpt = False,
    _version_flag: VersionOpt = False,
) -> None:
    """Bare invocation (no subcommand) opens the inline chat."""
    if no_color:
        os.environ["NO_COLOR"] = "1"  # rich honors it
    ctx.obj = CliState(env_file=env_file, json=json_output, no_color=no_color)
    # Persistent pre-run: require first-run acceptance of the disclaimer before anything runs
    # (#251). --help / --version are eager and short-circuit before this, so they're never gated.
    from gaia.legal import ensure_accepted

    ensure_accepted()
    if ctx.invoked_subcommand is None:
        _nudge_setup_if_unconfigured(ctx.obj)
        _chat(ctx.obj)


def _nudge_setup_if_unconfigured(st: CliState) -> None:
    """On bare invocation, if no model is configured yet, point the user at `gaia setup`."""
    from gaia.config import ConfigSupplier, get_settings

    settings = get_settings(st.env_file)
    cfg = ConfigSupplier(settings.config_path).current
    configured = (
        cfg.llm.openai.use_oauth or bool(settings.google_api_key) or bool(settings.openai_api_key)
    )
    if not configured:
        from gaia.cli._console import console

        console().print(
            "[yellow]gaia isn't configured yet.[/] Run [cyan]gaia setup[/] to pick a model + "
            "connectors, then [cyan]gaia start[/]."
        )


@app.command()
def chat(ctx: typer.Context) -> None:
    """Chat with Gaia in the local terminal."""
    _chat(state(ctx))


@app.command()
def dev(
    ctx: typer.Context,
    host: HostOpt = "127.0.0.1",
    port: PortOpt = 8000,
) -> None:
    """Launch ADK's dev web UI on Gaia — inspect tool calls and LLM requests live."""
    from gaia.app import run_dev

    run_dev(env_file=state(ctx).env_file, host=host, port=port)


@app.command()
def msg(
    ctx: typer.Context,
    text: TextArg,
    user: UserOpt = "local",
    channel: ChannelOpt = "cli",
    name: NameOpt = "",
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
        "version": gaia.version(),
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
