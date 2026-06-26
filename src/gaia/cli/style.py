"""``gaia style`` — show or set Gaia's communication style (voice)."""

from __future__ import annotations

from typing import Annotated

import typer

from gaia.cli._console import console
from gaia.cli._options import state

NameArg = Annotated[
    str | None, typer.Argument(help="Voice: human, caveman, or ai. Empty = show the current one.")
]


def style(ctx: typer.Context, name: NameArg = None) -> None:
    """Show or set Gaia's communication style (voice)."""
    from gaia.communication import STYLES, current_style, set_style
    from gaia.config import ConfigSupplier, get_settings

    out = console()
    settings = get_settings(state(ctx).env_file)

    if not name:
        cfg = ConfigSupplier(settings.config_path).current
        out.print(f"style: [bold]{current_style(cfg)}[/]  (options: {', '.join(STYLES)})")
        return

    chosen = name.strip().lower()
    try:
        set_style(settings.config_path, chosen)
    except ValueError as exc:
        out.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    out.print(f"style set to [bold]{chosen}[/] — in effect from the next message")
