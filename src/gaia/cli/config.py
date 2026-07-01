"""``gaia config`` command group — inspect & edit ``gaia.yaml`` from the terminal.

Raw key/value access to the whole config (the escape hatch behind the wizard + ``gaia model``).
``set`` uses the comment-preserving yaml editor; ``get`` reports the *effective* value (file +
schema defaults).

Lazy-import rule (repo convention): typer + stdlib (+ cli siblings) at module level.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from gaia.cli import _complete
from gaia.cli._console import console, emit_json
from gaia.cli._options import state

app = typer.Typer(name="config", help="Inspect and edit gaia.yaml.", no_args_is_help=True)

KeyArg = Annotated[
    str,
    typer.Argument(
        help="A dotted config key, e.g. 'llm.model' or 'missions.max_tasks'.",
        autocompletion=_complete.config_keys,
    ),
]
ValueArg = Annotated[
    str,
    typer.Argument(
        help="The value to set (written verbatim; the schema coerces it on load).",
        autocompletion=_complete.config_values,
    ),
]

_MISSING = object()


def _dig(data: Any, dotted: str) -> Any:
    """Walk a dotted key into a nested dict, or return the ``_MISSING`` sentinel."""
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


@app.command()
def path(ctx: typer.Context) -> None:
    """Print the path to the active gaia.yaml."""
    from gaia.config import get_settings

    config_path = get_settings(state(ctx).env_file).config_path
    if state(ctx).json:
        emit_json({"path": str(config_path), "exists": config_path.exists()})
    else:
        console().print(str(config_path))


@app.command()
def get(ctx: typer.Context, key: KeyArg) -> None:
    """Print one config value (the effective value, including schema defaults)."""
    from gaia.config import ConfigSupplier, get_settings

    config_path = get_settings(state(ctx).env_file).config_path
    data = ConfigSupplier(config_path).current.model_dump(mode="json")
    value = _dig(data, key)
    if value is _MISSING:
        console().print(f"no config key {key!r}")
        raise typer.Exit(1)
    if state(ctx).json:
        emit_json({key: value})
    else:
        console().print(value if isinstance(value, str) else repr(value))


@app.command("set")
def set_value(ctx: typer.Context, key: KeyArg, value: ValueArg) -> None:
    """Set a config value in gaia.yaml (comment-preserving)."""
    from gaia.cli._yamledit import set_config_value
    from gaia.config import get_settings

    config_path = get_settings(state(ctx).env_file).config_path
    set_config_value(config_path, key, value)
    console().print(f"updated: {key}={value}")
    console().print("a running daemon picks this up on its next turn (hot-reloaded).")


@app.command()
def edit(ctx: typer.Context) -> None:
    """Open gaia.yaml in $EDITOR; warn if the result doesn't parse."""
    import click

    from gaia.config import ConfigSupplier, get_settings

    config_path = get_settings(state(ctx).env_file).config_path
    click.edit(filename=str(config_path))
    try:
        _ = ConfigSupplier(config_path).current  # validates the just-saved file
    except Exception as exc:
        console().print(f"[red]warning: gaia.yaml has a problem — {exc}[/]")
        raise typer.Exit(1) from exc
    console().print(f"saved {config_path}")
