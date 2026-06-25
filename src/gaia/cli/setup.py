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
ApiKeyOpt = Annotated[str | None, typer.Option("--api-key", help="API key (non-interactive).")]
ProviderOpt = Annotated[
    str | None, typer.Option("--provider", help="Model provider: chatgpt | gemini | openai.")
]
ModelOpt = Annotated[
    str | None, typer.Option("--model", help="Model id (e.g. gpt-4o, gemini-2.5-flash).")
]


def _mask(value: str) -> str:
    """Show only the last 4 chars of a secret (the rest hidden)."""
    v = value.strip()
    return f"…{v[-4:]}" if len(v) > 4 else "****"


def _save_key(
    env_path: object, key_name: str, *, existing: str | None, flag: str | None, label: str
) -> str | None:
    """Persist a secret to ``.env``: use ``flag``, else (masked) confirm-overwrite + prompt.

    Returns the effective key (existing kept, or the new one), or ``None`` if none was given.
    """
    from gaia.cli._envfile import set_env_var

    key = (flag or "").strip() or None
    if key is None:
        if existing and not typer.confirm(f"{label} already set ({_mask(existing)}) — replace it?"):
            return existing  # keep the existing one
        key = typer.prompt(label, hide_input=True).strip() or None
    if key:
        set_env_var(env_path, key_name, key)  # type: ignore[arg-type]
        console().print(f"{label} saved")
    return key or existing


@app.command()
def search(ctx: typer.Context, engine: EngineOpt = None, api_key: ApiKeyOpt = None) -> None:
    """Set the web-search engine: 'duckduckgo' (no key) or 'brave' (needs an API key).

    Scriptable: `gaia setup search --engine brave --api-key <key>`. Bare command prompts.
    """
    from gaia import constants
    from gaia.cli._envfile import get_env_var
    from gaia.cli._select import select_one
    from gaia.cli._yamledit import set_config_value
    from gaia.config import get_settings
    from gaia.tools.web_search import SEARCH_ENGINES

    out = console()
    settings = get_settings(state(ctx).env_file)
    env_path = constants.ENV_FILE
    choices = ", ".join(sorted(SEARCH_ENGINES))

    eng = (engine or "").strip().lower()
    if not eng:
        picked = select_one(
            "Search engine",
            [
                ("duckduckgo", "DuckDuckGo", "no API key, privacy-first"),
                ("brave", "Brave", "needs a BRAVE_API_KEY (free tier)"),
            ],
            default="duckduckgo",
        )
        if picked is None:
            out.print("[yellow]cancelled[/]")
            raise typer.Exit(1)
        eng = picked
    if eng not in SEARCH_ENGINES:
        out.print(f"[red]unknown engine {eng!r}; available: {choices}[/]")
        raise typer.Exit(1)

    if eng == "brave":
        existing = get_env_var(env_path, "BRAVE_API_KEY") or settings.brave_api_key
        if not _save_key(
            env_path, "BRAVE_API_KEY", existing=existing, flag=api_key, label="Brave API key"
        ):
            out.print("[yellow]no Brave key — web_search stays off until BRAVE_API_KEY is set[/]")

    set_config_value(settings.config_path, "tools.web_search.engine", eng)
    out.print(f"web_search engine set to [bold]{eng}[/] — a running daemon hot-reloads it.")


@app.command()
def model(
    ctx: typer.Context,
    provider: ProviderOpt = None,
    api_key: ApiKeyOpt = None,
    model_id: ModelOpt = None,
) -> None:
    """Configure the LLM: Sign in with ChatGPT, or a Gemini / OpenAI API key.

    Scriptable: `gaia setup model --provider gemini --api-key <key> --model gemini-2.5-flash`.
    """
    from gaia import constants
    from gaia.cli._envfile import get_env_var
    from gaia.cli._select import select_one
    from gaia.cli._yamledit import set_config_value
    from gaia.config import get_settings

    out = console()
    settings = get_settings(state(ctx).env_file)
    env_path = constants.ENV_FILE
    cfg = settings.config_path

    choice = (provider or "").strip().lower()
    if not choice:
        choice = (
            select_one(
                "Model provider",
                [
                    ("chatgpt", "Sign in with ChatGPT", "subscription — no API key"),
                    ("gemini", "Google Gemini", "GEMINI_API_KEY"),
                    ("openai", "OpenAI", "OPENAI_API_KEY"),
                ],
                default="chatgpt",
            )
            or ""
        )

    if choice == "chatgpt":
        from gaia.app import run_auth

        run_auth("openai", env_file=state(ctx).env_file)  # device-code login, writes the token
        set_config_value(cfg, "llm.provider", "openai")
        set_config_value(cfg, "llm.openai.use_oauth", True)
        chosen = (model_id or "gpt-5.4-mini").strip()
        set_config_value(cfg, "llm.model", chosen)
        out.print(f"signed in with ChatGPT — model [bold]{chosen}[/]")
    elif choice == "gemini":
        existing = get_env_var(env_path, "GEMINI_API_KEY") or settings.google_api_key
        _save_key(
            env_path, "GEMINI_API_KEY", existing=existing, flag=api_key, label="Gemini API key"
        )
        set_config_value(cfg, "llm.provider", "gemini")
        set_config_value(cfg, "llm.openai.use_oauth", False)
        chosen = (model_id or "").strip() or typer.prompt(
            "Model", default="gemini-2.5-flash"
        ).strip()
        set_config_value(cfg, "llm.model", chosen)
        out.print(f"Gemini configured — model [bold]{chosen}[/]")
    elif choice == "openai":
        existing = get_env_var(env_path, "OPENAI_API_KEY") or settings.openai_api_key
        _save_key(
            env_path, "OPENAI_API_KEY", existing=existing, flag=api_key, label="OpenAI API key"
        )
        set_config_value(cfg, "llm.provider", "openai")
        set_config_value(cfg, "llm.openai.use_oauth", False)
        chosen = (model_id or "").strip() or typer.prompt("Model", default="gpt-4o").strip()
        set_config_value(cfg, "llm.model", chosen)
        out.print(f"OpenAI configured — model [bold]{chosen}[/]")
    else:
        out.print(f"[red]unknown provider {choice!r}; use chatgpt | gemini | openai[/]")
        raise typer.Exit(1)
    out.print("hot-reloaded — a running daemon picks it up next turn.")
