"""``gaia setup`` — guided configuration of gaia (model, connectors, search, tools).

Each step is a subcommand with **flags** (scriptable / testable) and an **interactive** fallback,
persisting through the shared writers: ``set_env_var`` (``~/.gaia/.env`` secrets, 0600) and
``set_config_value`` (``~/.gaia/gaia.yaml``, comment-preserving). Steps are non-destructive: an
already-set value is shown masked and only replaced after a confirm.

(First step shipped: ``search``. model / connectors / admin / browser / mcp follow the same way.)
"""

from __future__ import annotations

from pathlib import Path
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
    out.print(
        "[bold]gaia setup[/] — let's configure gaia. Esc/Ctrl-C skips a step (on to the next).\n"
    )
    from gaia.cli.tools import tools as tools_step

    # No Admin step: the owner is set automatically — the WhatsApp QR-scanner becomes admin, and on
    # any channel the first sender (when none exists yet) is bootstrapped as admin; cli is always
    # admin. `gaia setup admin --id …` remains for advanced/manual cases.
    for label, step in (
        ("Model", model),
        ("Memory", memory),
        ("Connectors", connectors),
        ("Tools", tools_step),
    ):
        out.print(f"\n[bold cyan]> {label}[/]")
        # Esc/Ctrl-C/decline in any step just skips it and moves on — never aborts the whole wizard.
        try:
            step(ctx)
        except (typer.Exit, typer.Abort, KeyboardInterrupt, EOFError):
            out.print(f"[dim]skipped {label.lower()}[/]")
    out.print(
        "\n[bold green]✓ setup complete.[/] Run [cyan]gaia doctor[/] to check, "
        "[cyan]gaia start[/] to launch."
    )


EngineOpt = Annotated[
    str | None, typer.Option("--engine", help="Search engine: duckduckgo | brave.")
]
ApiKeyOpt = Annotated[str | None, typer.Option("--api-key", help="API key (non-interactive).")]
ProviderOpt = Annotated[
    str | None, typer.Option("--provider", help="Model provider: openai | gemini.")
]
ModelOpt = Annotated[
    str | None, typer.Option("--model", help="Model id (e.g. gpt-4o, gemini-2.5-flash).")
]
EmbedderOpt = Annotated[
    str | None, typer.Option("--embedder", help="Memory embedder: gemini | openai.")
]
MemoryOnOpt = Annotated[
    bool | None, typer.Option("--on/--off", help="Turn long-term memory on or off.")
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


def memory(
    ctx: typer.Context,
    on: MemoryOnOpt = None,
    embedder: EmbedderOpt = None,
    api_key: ApiKeyOpt = None,
) -> None:
    """Wizard step - turn long-term memory on/off and pick its embedder (gemini = free, or openai).

    Reached via `gaia setup`; for non-interactive changes use the chat `/memory` command
    (on|off|gemini|openai) or `gaia config set memory.embedder.provider`.
    """
    from gaia import constants
    from gaia.cli._envfile import get_env_var
    from gaia.cli._select import select_one
    from gaia.cli._yamledit import set_config_value
    from gaia.config import ConfigSupplier, get_settings

    out = console()
    settings = get_settings(state(ctx).env_file)
    env_path = state(ctx).env_file or constants.ENV_FILE  # honor --env-file for secret writes
    cfg = settings.config_path
    mem = ConfigSupplier(cfg).current.memory

    # 1. on / off
    enabled = on
    if enabled is None:
        enabled = typer.confirm(
            "Turn on long-term memory? (Gaia remembers you across chats)", default=mem.enabled
        )
    set_config_value(cfg, "memory.enabled", enabled)
    if not enabled:
        out.print("long-term memory [yellow]off[/]")
        return

    # 2. pick the embedder (Gemini is free; OpenAI needs an OpenAI API key)
    prov = (embedder or "").strip().lower()
    if not prov:
        cur = mem.embedder.provider
        prov = (
            select_one(
                "Memory embedder",
                [
                    ("gemini", "Gemini", "free, recommended", "current" if cur == "gemini" else ""),
                    (
                        "openai",
                        "OpenAI",
                        "needs an OpenAI key",
                        "current" if cur == "openai" else "",
                    ),
                ],
                default=cur,
            )
            or cur
        )
    if prov not in ("gemini", "openai"):
        out.print(f"[red]unknown embedder {prov!r}; use gemini | openai[/]")
        raise typer.Exit(1)
    set_config_value(cfg, "memory.embedder.provider", prov)

    # 3. ensure the embedder's key (same env vars the model step uses)
    key_name = "GEMINI_API_KEY" if prov == "gemini" else "OPENAI_API_KEY"
    cur_key = settings.google_api_key if prov == "gemini" else settings.openai_api_key
    existing = get_env_var(env_path, key_name) or cur_key
    if prov == "gemini" and not existing:
        out.print(
            "Gemini embeddings are [green]free[/] - get a key at https://aistudio.google.com/apikey"
        )
    _save_key(env_path, key_name, existing=existing, flag=api_key, label=key_name)
    out.print(f"long-term memory [green]on[/] (embedder: {prov})")


def search(ctx: typer.Context, engine: EngineOpt = None, api_key: ApiKeyOpt = None) -> None:
    """Wizard step — set the web-search engine: 'duckduckgo' (no key) or 'brave' (needs an API key).

    Reached via `gaia setup`; not a standalone command (use `gaia config set
    tools.web_search.engine` for non-interactive changes).
    """
    from gaia import constants
    from gaia.cli._envfile import get_env_var
    from gaia.cli._select import select_one
    from gaia.cli._yamledit import set_config_value
    from gaia.config import ConfigSupplier, get_settings
    from gaia.tools.web_search import SEARCH_ENGINES

    out = console()
    settings = get_settings(state(ctx).env_file)
    env_path = state(ctx).env_file or constants.ENV_FILE  # honor --env-file for secret writes
    choices = ", ".join(sorted(SEARCH_ENGINES))
    ws = ConfigSupplier(settings.config_path).current.tools.get("web_search")
    cur_eng = getattr(ws, "engine", None) or "duckduckgo"  # the active engine, for the marker

    eng = (engine or "").strip().lower()
    if not eng:
        picked = select_one(
            "Search engine",
            [
                (
                    "duckduckgo",
                    "DuckDuckGo",
                    "no API key, privacy-first",
                    _cur(cur_eng, "duckduckgo"),
                ),
                ("brave", "Brave", "needs a BRAVE_API_KEY (free tier)", _cur(cur_eng, "brave")),
            ],
            default=cur_eng,
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


#: Curated model ids per provider for the picker ("Other…" lets you type any id).
_MODELS = {
    "openai": ["gpt-5.5", "gpt-5.4-mini", "gpt-4o", "gpt-4o-mini"],
    "gemini": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
}
#: ChatGPT-oauth (Codex backend, no /models endpoint) — its own curated list.
_OAUTH_MODELS = ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini"]

OAuthOpt = Annotated[
    bool, typer.Option("--oauth/--no-oauth", help="OpenAI: Sign in with ChatGPT (not an API key).")
]


#: Configurable auth "units" → (label, hint, provider, use_oauth). OpenAI offers two (ChatGPT
#: sign-in + API key); Gemini one. Configure several at once, then pick the active one.
_UNITS: dict[str, tuple[str, str, str, bool]] = {
    "chatgpt": ("ChatGPT (sign in)", "subscription — no API key", "openai", True),
    "openai": ("OpenAI (API key)", "OPENAI_API_KEY", "openai", False),
    "gemini": ("Google Gemini (API key)", "GEMINI_API_KEY", "gemini", False),
}
#: API-key env var + Settings attr for the key-based units.
_UNIT_KEY = {
    "openai": ("OPENAI_API_KEY", "openai_api_key"),
    "gemini": ("GEMINI_API_KEY", "google_api_key"),
}
#: Provider section headers (display order) + per-unit method label/hint, for the grouped picker.
_PROVIDER_LABEL = {"openai": "OpenAI", "gemini": "Google Gemini"}
_METHOD = {
    "chatgpt": ("Sign in with ChatGPT", "subscription — no API key"),
    "openai": ("API key", "OPENAI_API_KEY"),
    "gemini": ("API key", "GEMINI_API_KEY"),
}


def _provider_units(provider: str) -> list[str]:
    """Auth units offered by a provider (OpenAI → chatgpt + key; Gemini → key)."""
    return [u for u, (_l, _h, prov, _o) in _UNITS.items() if prov == provider]


def _provider_configured(provider: str, settings: object, env_path: Path) -> bool:
    return any(_unit_configured(u, settings, env_path) for u in _provider_units(provider))


def _choose_method(
    provider: str, ctx: typer.Context, settings: object, env_path: Path, cur_unit: str
) -> str | None:
    """Configure a provider: pick oauth-vs-key when it offers both, else prompt the key directly.

    Returns the configured unit, or ``None`` if the auth picker was cancelled.
    """
    from gaia.cli._select import select_one

    units = _provider_units(provider)
    if len(units) == 1:  # only one way in (Gemini) → prompt the key immediately
        _configure_unit(units[0], ctx, settings, env_path)
        return units[0]
    method = select_one(
        f"{_PROVIDER_LABEL[provider]} — how to authenticate",
        [
            (
                u,
                _METHOD[u][0],
                _METHOD[u][1],
                _badge(configured=_unit_configured(u, settings, env_path), current=u == cur_unit),
            )
            for u in units
        ],
        default=cur_unit if cur_unit in units else units[0],
    )
    if method is None:
        return None
    _configure_unit(method, ctx, settings, env_path)
    return method


def _badge(*, configured: bool, current: bool) -> str:
    """A picker status badge: 'configured' (key/session present) and/or 'current' (active)."""
    return " · ".join(
        p for p in ("configured" if configured else "", "current" if current else "") if p
    )


def _cur(actual: object, option: object) -> str:
    """The 'current' badge for the option that matches the active config value (else "")."""
    return "current" if actual == option else ""


def _unit_configured(unit: str, settings: object, env_path: Path) -> bool:
    from gaia.cli._envfile import get_env_var
    from gaia.providers.openai.store import credentials_path

    if unit == "chatgpt":
        return credentials_path().exists()
    env, attr = _UNIT_KEY[unit]
    return bool(get_env_var(env_path, env) or getattr(settings, attr))


def _current_unit(provider: str, use_oauth: bool) -> str:
    """The auth unit gaia is currently using (for the 'current' badge)."""
    if provider == "openai":
        return "chatgpt" if use_oauth else "openai"
    return "gemini"


def _configure_unit(
    unit: str, ctx: typer.Context, settings: object, env_path: Path, *, api_key: str | None = None
) -> None:
    """Run one unit's auth: ChatGPT device-login (already-signed-in check) or an API-key save."""
    from gaia.cli._envfile import get_env_var
    from gaia.providers.openai.store import credentials_path

    out = console()
    if unit == "chatgpt":
        if credentials_path().exists() and not typer.confirm(
            "ChatGPT session already configured — sign in again?", default=False
        ):
            out.print("kept the existing ChatGPT session")
            return
        from gaia.app import run_auth

        run_auth("openai", env_file=state(ctx).env_file)  # device-code login
        return
    env, attr = _UNIT_KEY[unit]
    label = "OpenAI API key" if unit == "openai" else "Gemini API key"
    existing = get_env_var(env_path, env) or getattr(settings, attr)
    _save_key(env_path, env, existing=existing, flag=api_key, label=label)


def _pick_model(
    provider: str, model_id: str | None, *, api_key: str | None, use_oauth: bool, current: str
) -> tuple[str | None, bool]:
    """Return (chosen model id, fell_back_to_curated). Flag path skips the fetch."""
    from gaia.cli._models import available_models
    from gaia.cli._select import select_one

    chosen = (model_id or "").strip()
    if chosen:
        return chosen, False
    fetched = available_models(provider, api_key=api_key, use_oauth=use_oauth)
    models = fetched or (_OAUTH_MODELS if use_oauth else _MODELS[provider])
    options: list[tuple[str, ...]] = [
        (m, m, "", _badge(configured=False, current=m == current)) for m in models
    ]
    options.append(("__custom__", "Other…", "type a model id", ""))
    picked = select_one("Model", options, default=current if current in models else models[0])
    if picked is None:
        return None, not fetched
    model = typer.prompt("Model id").strip() if picked == "__custom__" else picked
    return model, not fetched


def model(
    ctx: typer.Context,
    provider: ProviderOpt = None,
    oauth: OAuthOpt = False,
    api_key: ApiKeyOpt = None,
    model_id: ModelOpt = None,
) -> None:
    """Configure the LLM: pick a provider, authenticate (API key or ChatGPT sign-in), pick a model.

    Scriptable: `gaia model --provider gemini --api-key <key> --model gemini-2.5-flash`, or
    `gaia model --provider openai --oauth --model gpt-5.5`.
    """
    from gaia import constants
    from gaia.cli._envfile import get_env_var
    from gaia.cli._select import select_many, select_one
    from gaia.cli._yamledit import set_config_value
    from gaia.config import ConfigSupplier, get_settings

    out = console()
    settings = get_settings(state(ctx).env_file)
    env_path = state(ctx).env_file or constants.ENV_FILE  # honor --env-file for secret writes
    cfg = settings.config_path
    live = ConfigSupplier(cfg).current.llm
    cur_unit = _current_unit(live.provider, live.openai.use_oauth)

    # 1. choose which auth unit(s) to configure
    flag_prov = (provider or "").strip().lower()
    if flag_prov:  # scriptable single-unit path
        if flag_prov not in ("openai", "gemini"):
            out.print(f"[red]unknown provider {flag_prov!r}; use openai | gemini[/]")
            raise typer.Exit(1)
        unit = "chatgpt" if (flag_prov == "openai" and oauth) else flag_prov
        _configure_unit(unit, ctx, settings, env_path, api_key=api_key)
        units = [unit]
    else:  # interactive: pick provider(s) first, then each provider's auth method
        prov_marked = [p for p in _PROVIDER_LABEL if _provider_configured(p, settings, env_path)]
        prov_opts = [
            (p, label, "", "current" if p == live.provider else "")
            for p, label in _PROVIDER_LABEL.items()
        ]
        providers = select_many("Set up model providers", prov_opts, marked=prov_marked)
        if not providers:
            out.print("[yellow]nothing selected[/]")
            raise typer.Exit(1)
        for p in providers:
            _choose_method(p, ctx, settings, env_path, cur_unit)
        units = [u for u in _UNITS if _unit_configured(u, settings, env_path)]

    # 2. pick the ACTIVE unit (auto when only one configured)
    if len(units) == 1:
        active = units[0]
    else:
        picked = select_one(
            "Active provider",
            [
                (
                    u,
                    f"{_PROVIDER_LABEL[_UNITS[u][2]]} · {_METHOD[u][0]}",
                    "",
                    "current" if u == cur_unit else "",
                )
                for u in units
            ],
            default=cur_unit if cur_unit in units else units[0],
        )
        active = picked or units[0]

    _label, _hint, prov, use_oauth = _UNITS[active]
    set_config_value(cfg, "llm.provider", prov)
    set_config_value(cfg, "llm.openai.use_oauth", use_oauth)

    # 3. model for the active provider (fetched live; curated fallback)
    key = None
    if not use_oauth:
        env, attr = _UNIT_KEY[active]
        key = get_env_var(env_path, env) or getattr(settings, attr)
    chosen, fell_back = _pick_model(
        prov, model_id, api_key=key, use_oauth=use_oauth, current=live.model
    )
    if chosen is None:
        out.print("[yellow]cancelled[/]")
        raise typer.Exit(1)
    set_config_value(cfg, "llm.model", chosen)
    if fell_back and not (model_id or "").strip():
        out.print("[dim](couldn't fetch live models — showed the built-in defaults)[/]")
    out.print(f"active: [bold]{prov}[/] — model [bold]{chosen}[/] (hot-reloaded).")


# Not a subcommand — `gaia connect` is the connector command. The walkthrough runs it as a step.
def connectors(ctx: typer.Context) -> None:
    """Run the connector setup (delegates to `gaia connect`)."""
    from gaia.cli.connect import connect

    connect(ctx)  # interactive multi-select + per-connector credential flow (reused)


AdminIdOpt = Annotated[
    str | None, typer.Option("--id", help="Admin sender id as channel:id, e.g. telegram:12345.")
]


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


def browser(ctx: typer.Context, backend: BackendOpt = None, headless: HeadlessOpt = None) -> None:
    """Configure the browser tool: backend (mcp / native) and headless mode."""
    from gaia.cli._select import select_one
    from gaia.cli._yamledit import set_config_value
    from gaia.config import ConfigSupplier, get_settings

    out = console()
    cfg = get_settings(state(ctx).env_file).config_path
    live = ConfigSupplier(cfg).current.browser  # for the "current" markers

    be = (backend or "").strip().lower()
    if not be:
        be = (
            select_one(
                "Browser backend",
                [
                    (
                        "mcp",
                        "Playwright-MCP",
                        "full tool surface, needs bun",
                        _cur(live.backend, "mcp"),
                    ),
                    (
                        "native",
                        "Native",
                        "built-in, per-agent isolation",
                        _cur(live.backend, "native"),
                    ),
                ],
                default=live.backend,
            )
            or ""
        )
    if be not in ("mcp", "native"):
        out.print("[red]backend must be 'mcp' or 'native'[/]")
        raise typer.Exit(1)
    set_config_value(cfg, "browser.backend", be)

    hl = headless
    if hl is None and backend is None:  # interactive run: ask (default = the current value)
        hl = typer.confirm(
            "Run the browser headless (no visible window)?", default=bool(live.headless)
        )
    if hl is not None:
        set_config_value(cfg, "browser.headless", hl)
    out.print(
        f"browser backend set to [bold]{be}[/]" + (f", headless={hl}" if hl is not None else "")
    )


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
