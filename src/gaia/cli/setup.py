"""``gaia setup`` — guided configuration of gaia (model, connectors, search, tools).

Each step is a subcommand with **flags** (scriptable / testable) and an **interactive** fallback,
persisting through the shared writers: ``set_env_var`` (``~/.gaia/.env`` secrets, 0600) and
``set_config_value`` (``~/.gaia/gaia.yaml``, comment-preserving). Steps are non-destructive: an
already-set value is shown masked and only replaced after a confirm.

(First step shipped: ``search``. model / connectors / admin / browser / mcp follow the same way.)
"""

from __future__ import annotations

from typing import Annotated

import typer

from gaia.cli._console import console
from gaia.cli._options import state

app = typer.Typer(
    name="setup", help="Configure gaia: model, connectors, search, tools.", no_args_is_help=True
)

EngineOpt = Annotated[
    str | None, typer.Option("--engine", help="Search engine: duckduckgo | brave.")
]
ApiKeyOpt = Annotated[str | None, typer.Option("--api-key", help="API key for the engine (brave).")]


def _mask(value: str) -> str:
    """Show only the last 4 chars of a secret (the rest hidden)."""
    v = value.strip()
    return f"…{v[-4:]}" if len(v) > 4 else "****"


@app.command()
def search(ctx: typer.Context, engine: EngineOpt = None, api_key: ApiKeyOpt = None) -> None:
    """Set the web-search engine: 'duckduckgo' (no key) or 'brave' (needs an API key).

    Scriptable: `gaia setup search --engine brave --api-key <key>`. Bare command prompts.
    """
    from gaia import constants
    from gaia.cli._envfile import get_env_var, set_env_var
    from gaia.cli._yamledit import set_config_value
    from gaia.config import get_settings
    from gaia.tools.web_search import SEARCH_ENGINES

    out = console()
    settings = get_settings(state(ctx).env_file)
    env_path = constants.ENV_FILE
    choices = ", ".join(sorted(SEARCH_ENGINES))

    eng = (engine or "").strip().lower()
    if not eng:
        eng = typer.prompt(f"Search engine ({choices})", default="duckduckgo").strip().lower()
    if eng not in SEARCH_ENGINES:
        out.print(f"[red]unknown engine {eng!r}; available: {choices}[/]")
        raise typer.Exit(1)

    if eng == "brave":
        existing = get_env_var(env_path, "BRAVE_API_KEY") or settings.brave_api_key
        key = (api_key or "").strip() or None
        if key is None:
            if existing and not typer.confirm(
                f"Brave API key already set ({_mask(existing)}) — replace it?"
            ):
                key = None  # keep the existing one
            else:
                key = typer.prompt("Brave API key", hide_input=True).strip() or None
        if key:
            set_env_var(env_path, "BRAVE_API_KEY", key)
            out.print("Brave API key saved")
        elif not existing:
            out.print("[yellow]no Brave key — web_search stays off until BRAVE_API_KEY is set[/]")

    set_config_value(settings.config_path, "tools.web_search.engine", eng)
    out.print(f"web_search engine set to [bold]{eng}[/] — a running daemon hot-reloads it.")
