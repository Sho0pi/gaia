"""``gaia llm`` command group: model-provider operations (only ``auth`` for now).

The rest of the group (status/set/list) lands with the llm-group issue of the CLI epic.
"""

from __future__ import annotations

from typing import Annotated

import typer

from gaia.cli._options import state

app = typer.Typer(name="llm", help="Model provider operations.", no_args_is_help=True)

ProviderArg = Annotated[str, typer.Argument(help='Provider to sign in to (e.g. "openai").')]


@app.command()
def auth(
    ctx: typer.Context,
    provider: ProviderArg,
) -> None:
    """Interactive provider login; stores credentials under ~/.gaia."""
    from gaia.app import run_auth

    run_auth(provider, env_file=state(ctx).env_file)
