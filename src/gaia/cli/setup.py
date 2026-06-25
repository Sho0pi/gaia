"""``gaia setup`` — guided configuration of gaia (model, connectors, search, tools).

Each step is a subcommand with **flags** (scriptable / testable) and an **interactive** fallback,
persisting through the shared writers: ``set_env_var`` (``~/.gaia/.env`` secrets, 0600) and
``set_config_value`` (``~/.gaia/gaia.yaml``, comment-preserving). Steps are non-destructive: an
already-set value is shown masked and only replaced after a confirm.

(First step shipped: ``search``. model / connectors / admin / browser / mcp follow the same way.)
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from gaia.cli._console import console
from gaia.cli._options import state

app = typer.Typer(
    name="setup", help="Configure gaia: model, connectors, search, tools.", no_args_is_help=False
)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Run the full guided setup when called with no subcommand; otherwise dispatch the step."""
    if ctx.invoked_subcommand is not None:
        return
    out = console()
    out.print("[bold]gaia setup[/] — let's configure gaia. Esc/Ctrl-C any step to skip it.\n")
    for label, step in (
        ("Model", model),
        ("Connectors", connectors),
        ("Admin", admin),
        ("Search", search),
        ("Browser", browser),
    ):
        out.print(f"\n[bold cyan]> {label}[/]")
        try:
            step(ctx)
        except typer.Exit:
            out.print(f"[dim]skipped {label.lower()}[/]")
        except (KeyboardInterrupt, EOFError):
            out.print("\n[yellow]setup cancelled[/]")
            raise typer.Exit(1) from None
    if typer.confirm("\nAdd a custom MCP server?", default=False):
        try:
            mcp(ctx)
        except typer.Exit:
            pass
    out.print(
        "\n[bold green]✓ setup complete.[/] Run [cyan]gaia doctor[/] to check, "
        "[cyan]gaia start[/] to launch."
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


@app.command()
def connectors(ctx: typer.Context) -> None:
    """Set up messaging connectors (Telegram, WhatsApp) — runs the connect flow."""
    from gaia.cli.connect import connect

    connect(ctx)  # interactive multi-select + per-connector credential flow (reused)


AdminIdOpt = Annotated[
    str | None, typer.Option("--id", help="Admin sender id as channel:id, e.g. telegram:12345.")
]


@app.command()
def admin(ctx: typer.Context, admin_id: AdminIdOpt = None) -> None:
    """Set the admin user (full access; receives monitor DMs and runs admin commands).

    Scriptable: `gaia setup admin --id telegram:12345`. Bare command prompts for channel + id.
    """
    from gaia.cli._select import select_one
    from gaia.cli._yamledit import set_config_value
    from gaia.config import ConfigSupplier, get_settings

    out = console()
    cfg = get_settings(state(ctx).env_file).config_path
    current = ConfigSupplier(cfg).current.admin

    value = (admin_id or "").strip()
    if not value:
        if current and not typer.confirm(f"admin already set ({', '.join(current)}) — replace it?"):
            out.print("kept the existing admin")
            return
        channel = select_one(
            "Your channel",
            [
                ("telegram", "Telegram", ""),
                ("whatsapp", "WhatsApp", ""),
                ("cli", "CLI (local terminal)", ""),
            ],
            default="telegram",
        )
        if channel is None:
            out.print("[yellow]cancelled[/]")
            raise typer.Exit(1)
        ident = typer.prompt(f"Your {channel} sender id").strip()
        if not ident:
            out.print("[yellow]no id given — admin not set[/]")
            return
        value = f"{channel}:{ident}"

    set_config_value(cfg, "admin", [value])
    out.print(f"admin set to [bold]{value}[/] — monitor DMs + admin commands now target you.")


BackendOpt = Annotated[str | None, typer.Option("--backend", help="Browser backend: mcp | native.")]
HeadlessOpt = Annotated[
    bool | None, typer.Option("--headless/--no-headless", help="Run the browser headless.")
]


@app.command()
def browser(ctx: typer.Context, backend: BackendOpt = None, headless: HeadlessOpt = None) -> None:
    """Configure the browser tool: backend (mcp / native) and headless mode."""
    from gaia.cli._select import select_one
    from gaia.cli._yamledit import set_config_value
    from gaia.config import get_settings

    out = console()
    cfg = get_settings(state(ctx).env_file).config_path

    be = (backend or "").strip().lower()
    if not be:
        be = (
            select_one(
                "Browser backend",
                [
                    ("mcp", "Playwright-MCP", "full tool surface, needs bun"),
                    ("native", "Native", "gaia's built-in tools, per-agent isolation"),
                ],
                default="mcp",
            )
            or ""
        )
    if be not in ("mcp", "native"):
        out.print("[red]backend must be 'mcp' or 'native'[/]")
        raise typer.Exit(1)
    set_config_value(cfg, "browser.backend", be)

    hl = headless
    if hl is None and backend is None:  # interactive run: ask
        hl = typer.confirm("Run the browser headless (no visible window)?", default=True)
    if hl is not None:
        set_config_value(cfg, "browser.headless", hl)
    out.print(
        f"browser backend set to [bold]{be}[/]" + (f", headless={hl}" if hl is not None else "")
    )


@app.command()
def mcp(
    ctx: typer.Context,
    name: Annotated[str | None, typer.Option("--name", help="Short server id.")] = None,
    transport: Annotated[
        str | None, typer.Option("--transport", help="stdio | http | sse.")
    ] = None,
    command: Annotated[str | None, typer.Option("--command", help="stdio: the executable.")] = None,
    arg: Annotated[list[str] | None, typer.Option("--arg", help="stdio: arg (repeatable).")] = None,
    url: Annotated[str | None, typer.Option("--url", help="http/sse: server URL.")] = None,
) -> None:
    """Add a custom MCP server to gaia.yaml (appends to mcp.servers; needs a daemon restart)."""
    from gaia.cli._select import select_one
    from gaia.cli._yamledit import set_config_value
    from gaia.config import ConfigSupplier, get_settings

    out = console()
    cfg = get_settings(state(ctx).env_file).config_path

    nm = (name or "").strip() or typer.prompt("Server name (short id)").strip()
    if not nm:
        out.print("[yellow]no name — cancelled[/]")
        raise typer.Exit(1)
    tr = (transport or "").strip().lower()
    if not tr:
        tr = (
            select_one(
                "Transport",
                [
                    ("stdio", "stdio", "local command"),
                    ("http", "http", "remote URL"),
                    ("sse", "sse", "remote URL"),
                ],
                default="stdio",
            )
            or "stdio"
        )

    server: dict[str, Any] = {"name": nm, "transport": tr}
    if tr == "stdio":
        server["command"] = (command or "").strip() or typer.prompt("Command (e.g. bunx)").strip()
        args_list = list(arg or [])
        if not args_list and name is None:  # interactive run
            raw = typer.prompt("Args (space-separated)", default="").strip()
            args_list = raw.split() if raw else []
        if args_list:
            server["args"] = args_list
    else:
        server["url"] = (url or "").strip() or typer.prompt("Server URL").strip()

    current = [s.model_dump(exclude_defaults=True) for s in ConfigSupplier(cfg).current.mcp.servers]
    set_config_value(cfg, "mcp.servers", [*current, server])
    out.print(f"added MCP server [bold]{nm}[/] ({tr}) — restart the daemon to attach it.")
