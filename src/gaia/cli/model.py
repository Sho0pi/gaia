"""``gaia model`` — interactive LLM provider/model setup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from gaia import constants
from gaia.cli._console import console
from gaia.cli._envfile import get_env_var, set_env_var
from gaia.cli._options import state
from gaia.cli._yamledit import set_config_value

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.config import Settings


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    label: str
    env_key: str
    fallback_models: tuple[str, ...]


PROVIDERS: dict[str, ProviderSpec] = {
    "gemini": ProviderSpec(
        "gemini",
        "Google Gemini",
        "GEMINI_API_KEY",
        ("gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"),
    ),
    "openai": ProviderSpec(
        "openai",
        "OpenAI",
        "OPENAI_API_KEY",
        ("gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini"),
    ),
    "anthropic": ProviderSpec(
        "anthropic",
        "Anthropic",
        "ANTHROPIC_API_KEY",
        ("claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"),
    ),
}


@dataclass(frozen=True)
class ConfiguredProvider:
    provider: str
    models: list[str]
    use_oauth: bool = False


def model(
    ctx: typer.Context,
    providers: Annotated[
        list[str] | None,
        typer.Argument(help="Providers (gemini/openai/anthropic); empty = pick from a menu."),
    ] = None,
    api_key: Annotated[
        str | None, typer.Option("--api-key", help="API key when configuring one provider.")
    ] = None,
    no_fetch: Annotated[
        bool, typer.Option("--no-fetch", help="Skip live model fetch; use curated fallback.")
    ] = False,
) -> None:
    """Set up model providers, auth, and the default model."""
    from gaia.config import get_settings

    st = state(ctx)
    settings = get_settings(st.env_file)
    out = console()
    selected = list(providers or [])
    for name in selected:
        if name not in PROVIDERS:
            out.print(f"unknown provider {name!r} — choose from: {', '.join(PROVIDERS)}")
            raise typer.Exit(2)
    if not selected:
        selected = _choose_providers(settings)
        if not selected:
            out.print("nothing selected — bye")
            return

    configured: list[ConfiguredProvider] = []
    for name in selected:
        result = _configure_provider(
            settings, name, api_key=api_key, fetch=not no_fetch, env_file=st.env_file
        )
        if result is not None:
            configured.append(result)

    if not configured:
        raise typer.Exit(1)

    default = _choose_default(configured)
    set_config_value(settings.config_path, "llm.provider", default.provider)
    set_config_value(settings.config_path, "llm.model", default.model)
    set_config_value(settings.config_path, "llm.openai.use_oauth", default.use_oauth)
    out.print(f"\n[bold green]default model:[/] {default.provider} / {default.model}")


@dataclass(frozen=True)
class DefaultChoice:
    provider: str
    model: str
    use_oauth: bool = False


def _choose_providers(settings: Settings) -> list[str]:
    rows = [(name, spec.label, _status(settings, spec)) for name, spec in PROVIDERS.items()]
    if console().is_terminal:
        return _prompt_checkbox("Which providers?", rows)
    return _choose_numbered("Providers", rows)


def _choose_default(configured: list[ConfiguredProvider]) -> DefaultChoice:
    provider_rows = [
        (c.provider, PROVIDERS[c.provider].label, f"{len(c.models)} models") for c in configured
    ]
    provider = _prompt_one("Default provider", provider_rows)
    chosen = next(c for c in configured if c.provider == provider)
    model_rows = [(m, m, "") for m in chosen.models]
    model_id = _prompt_one(f"Default {provider} model", model_rows)
    return DefaultChoice(provider, model_id, chosen.use_oauth)


def _prompt_checkbox(title: str, rows: list[tuple[str, str, str]]) -> list[str]:
    return _prompt_rows(title, rows, multi=True)


def _prompt_one(title: str, rows: list[tuple[str, str, str]]) -> str:
    if console().is_terminal:
        picked = _prompt_rows(title, rows, multi=False)
    else:
        picked = _choose_numbered(title, rows, single=True)
    if not picked:
        raise typer.Exit(1)
    return picked[0]


def _prompt_rows(title: str, rows: list[tuple[str, str, str]], *, multi: bool) -> list[str]:
    """Inline prompt_toolkit picker: arrows move, space toggles, enter submits."""
    from prompt_toolkit import Application
    from prompt_toolkit.formatted_text import StyleAndTextTuples
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    cursor = 0
    selected: set[int] = set()

    def current() -> list[str]:
        active = selected or {cursor}
        return [rows[i][0] for i in range(len(rows)) if i in active]

    def text() -> StyleAndTextTuples:
        mode = "space select · enter submit" if multi else "enter choose"
        fragments: StyleAndTextTuples = [
            ("class:title", title),
            ("class:help", f"  ↑/↓ move · {mode} · esc cancel\n"),
        ]
        for i, (_key, label, status) in enumerate(rows):
            row_style = "class:selected" if i == cursor else ""
            pointer = ">" if i == cursor else " "
            mark = "x" if i in selected else " "
            suffix = f" — {status}" if status else ""
            fragments.append((row_style, f"{pointer} {mark} "))
            fragments.append(("class:name", label))
            fragments.append(("class:status", f"{suffix}\n"))
        return fragments

    control = FormattedTextControl(text, focusable=True)
    app: Application[list[str]]
    kb = KeyBindings()

    @kb.add("up")
    def _up(_event: object) -> None:
        nonlocal cursor
        cursor = (cursor - 1) % len(rows)
        app.invalidate()

    @kb.add("down")
    def _down(_event: object) -> None:
        nonlocal cursor
        cursor = (cursor + 1) % len(rows)
        app.invalidate()

    @kb.add(" ")
    def _space(_event: object) -> None:
        if multi:
            selected.symmetric_difference_update({cursor})
        else:
            selected.clear()
            selected.add(cursor)
        app.invalidate()

    @kb.add("enter")
    def _enter(_event: object) -> None:
        app.exit(result=current())

    @kb.add("escape")
    @kb.add("c-c")
    @kb.add("c-d")
    def _cancel(_event: object) -> None:
        app.exit(result=[])

    style = Style.from_dict(
        {
            "title": "bold #7aa2f7",
            "help": "#565f89",
            "name": "#9ece6a",
            "status": "#c0caf5",
            "selected": "reverse",
        }
    )
    app = Application(
        layout=Layout(Window(control, always_hide_cursor=True)),
        key_bindings=kb,
        style=style,
        full_screen=False,
    )
    return app.run()


def _choose_numbered(
    title: str, rows: list[tuple[str, str, str]], *, single: bool = False
) -> list[str]:
    out = console()
    out.print(f"{title}:")
    for i, (_key, label, status) in enumerate(rows, 1):
        suffix = f" — [dim]{status}[/]" if status else ""
        out.print(f"  [cyan]{i}[/]. [green]{label}[/]{suffix}")
    prompt = "Select number" if single else "Select (comma-separated numbers, e.g. 1,2)"
    raw = typer.prompt(prompt, default="")
    picked: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if token.isdigit() and 1 <= int(token) <= len(rows):
            picked.append(rows[int(token) - 1][0])
            if single:
                break
    return picked


def _status(settings: Settings, spec: ProviderSpec) -> str:
    existing = get_env_var(constants.ENV_FILE, spec.env_key) or _settings_key(settings, spec.name)
    if spec.name == "openai" and _openai_oauth_configured():
        return "configured (OAuth)" if not existing else "configured (API key + OAuth)"
    return "configured" if existing else "not configured"


def _openai_oauth_configured() -> bool:
    from gaia.providers.openai.store import load_credentials

    return load_credentials() is not None


def _settings_key(settings: Settings, provider: str) -> str | None:
    if provider == "gemini":
        return settings.google_api_key
    if provider == "openai":
        return settings.openai_api_key
    if provider == "anthropic":
        return settings.anthropic_api_key
    return None


def _configure_provider(
    settings: Settings,
    provider: str,
    *,
    api_key: str | None,
    fetch: bool,
    env_file: Path | None = None,
) -> ConfiguredProvider | None:
    spec = PROVIDERS[provider]
    use_oauth = False
    key = get_env_var(constants.ENV_FILE, spec.env_key) or _settings_key(settings, provider)

    model_token = get_env_var(constants.ENV_FILE, spec.env_key) or api_key
    if provider == "openai":
        method = _choose_openai_method(key is not None)
        if method == "oauth":
            if _openai_oauth_configured() and not typer.confirm(
                "OpenAI OAuth is already configured — sign in again?"
            ):
                console().print("kept existing OpenAI OAuth credentials")
            else:
                from gaia.app import run_auth

                run_auth("openai", env_file=env_file)
            use_oauth = True
            model_token = None
            if fetch:
                console().print(
                    "[yellow]OpenAI OAuth cannot list API models (api.openai.com returns 403); "
                    "using curated ChatGPT model list[/]"
                )
        elif not _ensure_api_key(spec, key, api_key):
            return None
        else:
            model_token = get_env_var(constants.ENV_FILE, spec.env_key) or api_key
    elif not _ensure_api_key(spec, key, api_key):
        return None
    else:
        model_token = get_env_var(constants.ENV_FILE, spec.env_key) or api_key

    models = _models_for(provider, model_token, fetch)
    return ConfiguredProvider(provider, models, use_oauth=use_oauth)


def _choose_openai_method(has_key: bool) -> str:
    rows = [
        ("api_key", "API key", "configured" if has_key else "not configured"),
        (
            "oauth",
            "OAuth",
            "configured" if _openai_oauth_configured() else "ChatGPT sign-in",
        ),
    ]
    return _prompt_one("OpenAI auth method", rows)


def _ensure_api_key(spec: ProviderSpec, existing: str | None, api_key: str | None) -> bool:
    out = console()
    if existing and api_key is None:
        if not typer.confirm(f"{spec.label} API key already configured — replace it?"):
            out.print(f"kept existing {spec.label} key")
            return True
    value = api_key or typer.prompt(f"{spec.label} API key", hide_input=True).strip()
    if not value:
        out.print(f"[yellow]no key given — skipping {spec.name}[/]")
        return False
    set_env_var(constants.ENV_FILE, spec.env_key, value)
    return True


def _models_for(provider: str, api_key: str | None, fetch: bool) -> list[str]:
    spec = PROVIDERS[provider]
    if fetch and api_key:
        try:
            models = _fetch_models(provider, api_key)
            if models:
                return models
        except Exception as exc:
            console().print(f"[yellow]could not fetch {provider} models: {exc}; using fallback[/]")
    return list(spec.fallback_models)


def _fetch_models(provider: str, api_key: str) -> list[str]:
    if provider == "gemini":
        return _fetch_gemini_models(api_key)
    if provider == "openai":
        return _fetch_openai_models(api_key)
    if provider == "anthropic":
        return _fetch_anthropic_models(api_key)
    return []


def _fetch_gemini_models(api_key: str) -> list[str]:
    import httpx

    resp = httpx.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": api_key},
        timeout=10,
    )
    resp.raise_for_status()
    return _parse_gemini_models(resp.json())


def _parse_gemini_models(data: dict[str, object]) -> list[str]:
    models = []
    items = data.get("models", [])
    if not isinstance(items, list):
        return []
    for item in items:
        if not isinstance(item, dict):
            continue
        methods = item.get("supportedGenerationMethods", [])
        name = str(item.get("name", "")).removeprefix("models/")
        if name.startswith("gemini-") and "generateContent" in methods:
            models.append(name)
    return sorted(set(models))


def _fetch_openai_models(api_key: str) -> list[str]:
    import httpx

    resp = httpx.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10,
    )
    resp.raise_for_status()
    return _parse_openai_models(resp.json())


def _parse_openai_models(data: dict[str, object]) -> list[str]:
    items = data.get("data", [])
    if not isinstance(items, list):
        return []
    ids = [str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")]
    preferred = [m for m in ids if m.startswith(("gpt-", "o"))]
    return sorted(set(preferred or ids))


def _fetch_anthropic_models(api_key: str) -> list[str]:
    import httpx

    resp = httpx.get(
        "https://api.anthropic.com/v1/models",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
        timeout=10,
    )
    resp.raise_for_status()
    return _parse_anthropic_models(resp.json())


def _parse_anthropic_models(data: dict[str, object]) -> list[str]:
    items = data.get("data", [])
    if not isinstance(items, list):
        return []
    return sorted(str(item["id"]) for item in items if isinstance(item, dict) and item.get("id"))
