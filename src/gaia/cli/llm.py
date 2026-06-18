"""``gaia llm`` command group: model-provider operations — status, set, list, auth.

Lazy-import rule (repo convention): typer + stdlib (+ cli siblings) at module level.
"""

from __future__ import annotations

from typing import Annotated

import typer

from gaia.cli._console import console, emit_json
from gaia.cli._options import state

app = typer.Typer(name="llm", help="Model provider operations.", no_args_is_help=True)

#: Known providers (provider is a free-form string in the schema — litellm ids also work).
_PROVIDERS = {
    "gemini": "Google Gemini (GEMINI_API_KEY)",
    "openai": "OpenAI / Sign in with ChatGPT (needs the 'llm' dep group)",
}

ProviderArg = Annotated[str, typer.Argument(help='Provider to sign in to (e.g. "openai").')]
ModelOpt = Annotated[str, typer.Option("--model", help="Model id (e.g. gpt-4o, gemini-2.5-flash).")]
ProviderOpt = Annotated[
    str, typer.Option("--provider", help="Provider id (gemini / openai / any litellm id).")
]


@app.command()
def status(ctx: typer.Context) -> None:
    """Show the active provider and model."""
    from gaia.config import ConfigSupplier, get_settings

    cfg = ConfigSupplier(get_settings(state(ctx).env_file).config_path).current
    if state(ctx).json:
        emit_json({"provider": cfg.llm.provider, "model": cfg.llm.model})
        return
    out = console()
    out.print(f"provider: {cfg.llm.provider}")
    out.print(f"model: {cfg.llm.model or '(default)'}")


@app.command("set")
def set_model(
    ctx: typer.Context,
    model: ModelOpt = "",
    provider: ProviderOpt = "",
) -> None:
    """Set the active model and/or provider in gaia.yaml (comment-preserving)."""
    from gaia.cli._yamledit import set_config_value
    from gaia.config import get_settings

    if not model and not provider:
        console().print("nothing to set — pass --model and/or --provider")
        raise typer.Exit(2)
    path = get_settings(state(ctx).env_file).config_path
    if model:
        set_config_value(path, "llm.model", model)
    if provider:
        set_config_value(path, "llm.provider", provider)
    changed = ", ".join(
        p
        for p in (f"model={model}" if model else "", f"provider={provider}" if provider else "")
        if p
    )
    console().print(f"updated: {changed}")


@app.command("list")
def list_providers(ctx: typer.Context) -> None:
    """List the documented model providers."""
    if state(ctx).json:
        emit_json({"providers": _PROVIDERS})
        return
    out = console()
    for name, desc in _PROVIDERS.items():
        out.print(f"{name} — {desc}")
    out.print("\nany other litellm provider id also works; keys live in env (~/.gaia/.env)")


@app.command()
def auth(
    ctx: typer.Context,
    provider: ProviderArg,
) -> None:
    """Interactive provider login; stores credentials under ~/.gaia."""
    from gaia.app import run_auth

    run_auth(provider, env_file=state(ctx).env_file)
