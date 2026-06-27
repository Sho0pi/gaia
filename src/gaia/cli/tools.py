"""``gaia tools`` — one home for tool configuration.

A gaia-themed multi-select of the tools you can configure (emoji + one-line brief), matching the
model/connector pickers. **space** = (re)configure a tool / enable a simple one; **backspace** =
reset to defaults / disable; **enter** = apply; Esc/Ctrl-C = no changes. ``--all`` adds the optional
on/off tools to the same list. The ``gaia setup`` wizard's "Tools" step calls :func:`tools`.

Lazy-import rule (repo convention): typer + stdlib (+ cli siblings) at module level; the per-tool
configure flows live in ``cli/setup.py``, imported lazily to avoid an import cycle (the setup.py
wizard calls back into this module).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

from gaia.cli._console import console
from gaia.cli._options import state

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    from gaia.config import GaiaConfig

#: Configurable tools (have an interactive flow): id → (emoji, label, brief).
_CONFIGURABLE = {
    "browser": ("🌐", "browser", "drive a real web browser"),
    "web_search": ("🔎", "web_search", "search the web"),
    "generate_image": ("🎨", "image", "generate images"),
    "mcp": ("🧩", "mcp", "custom MCP servers (extra tools)"),
}
#: Optional on/off tools surfaced under ``--all`` (no flow — just enable/disable): id → (emoji, …).
_TOGGLEABLE = {
    "web_fetch": ("📄", "web_fetch", "fetch a URL's contents"),
    "remember": ("🧠", "remember", "save long-term memories"),
    "cron": ("⏰", "cron", "schedule recurring tasks"),
    "serve": ("🌍", "serve", "host files on a public URL"),
    "run_command": ("🐚", "shell", "run shell commands (guarded)"),
    "ask_user": ("❓", "ask_user", "ask you a clarifying question"),
}

AllOpt = Annotated[
    bool, typer.Option("--all", help="Enable/disable the optional on/off tools too.")
]


def tools(ctx: typer.Context, show_all: AllOpt = False) -> None:
    """Configure tools: browser, web search, custom MCP servers (and `--all` to toggle the rest).

    space = (re)configure a tool / enable a simple one; backspace = reset / disable; enter = apply.
    Esc/Ctrl-C cancels with no changes.
    """
    from gaia.cli._select import select_manage
    from gaia.config import ConfigSupplier, get_settings

    cfg_path = get_settings(state(ctx).env_file).config_path
    cfg = ConfigSupplier(cfg_path).current

    # default = configurable tools + serve (public link sharing — a notable feature, so always
    # shown despite being a plain toggle); --all also lists the remaining optional on/off tools.
    extra = list(_TOGGLEABLE) if show_all else ["serve"]
    ids = list(_CONFIGURABLE) + extra
    rows = [
        (tid, f"{_meta(tid)[0]} {_meta(tid)[1]}", _meta(tid)[2], _active_badge(cfg, tid))
        for tid in ids
    ]
    marked = [tid for tid in ids if _active(cfg, tid)]

    # select_manage cancels with ([], []) — so Esc/Ctrl-C is a true no-op (never mass-disables).
    to_act, to_deact = select_manage("Tools", rows, marked=marked)
    for tid in to_act:
        _act(tid, ctx, cfg_path)
    for tid in to_deact:
        _deact(tid, cfg_path)


def _meta(tid: str) -> tuple[str, str, str]:
    """(emoji, label, brief) for a tool id, from whichever table it lives in."""
    return _CONFIGURABLE.get(tid) or _TOGGLEABLE[tid]


def _active(cfg: GaiaConfig, tid: str) -> bool:
    """Whether a tool shows the `―` marker: configurable → has settings; toggle → enabled."""
    return _configured(cfg, tid) if tid in _CONFIGURABLE else _enabled(cfg, tid)


def _active_badge(cfg: GaiaConfig, tid: str) -> str:
    return "configured" if _active(cfg, tid) else ""


def _act(tid: str, ctx: typer.Context, cfg_path: Path) -> None:
    """space → configure a configurable tool, or enable a simple one."""
    if tid in _CONFIGURABLE:
        _configure(tid, ctx)
    else:
        from gaia.cli._yamledit import set_config_value

        set_config_value(cfg_path, f"tools.{tid}.enabled", True)
        console().print(f"enabled [bold]{tid}[/]")


def _deact(tid: str, cfg_path: Path) -> None:
    """backspace → reset a configurable tool to defaults, or disable a simple one."""
    if tid in _CONFIGURABLE:
        _reset(tid, cfg_path)
    else:
        from gaia.cli._yamledit import set_config_value

        set_config_value(cfg_path, f"tools.{tid}.enabled", False)
        console().print(f"disabled [bold]{tid}[/]")


def _configured(cfg: GaiaConfig, tid: str) -> bool:
    """Whether a configurable tool has real settings (so it shows the `―` 'configured' marker)."""
    if tid == "web_search":
        ws = cfg.tools.get("web_search")
        return bool(ws and getattr(ws, "engine", None))
    if tid == "generate_image":
        img = cfg.tools.get("generate_image")
        return bool(img and getattr(img, "provider", None))
    if tid == "mcp":
        return bool(cfg.mcp.servers)
    if tid == "browser":
        return True  # always available (backend has a default)
    return False


def _configure(tid: str, ctx: typer.Context) -> None:
    """Run a configurable tool's flow (reuses the wizard step functions in cli/setup.py)."""
    from gaia.cli import setup

    out = console()
    out.print(f"\n[bold]{_CONFIGURABLE[tid][0]} {_CONFIGURABLE[tid][1]}[/]")
    if tid == "browser":
        setup.browser(ctx)
    elif tid == "web_search":
        setup.search(ctx)
    elif tid == "generate_image":
        _configure_image(ctx)
    elif tid == "mcp":
        _manage_mcp(ctx)


def _reset(tid: str, cfg_path: Path) -> None:
    """Reset a tool's settings to gaia's defaults (comment-preserving)."""
    from gaia.cli._yamledit import set_config_value

    out = console()
    if tid == "web_search":
        set_config_value(cfg_path, "tools.web_search.engine", "duckduckgo")  # the no-key default
    elif tid == "generate_image":
        set_config_value(cfg_path, "tools.generate_image.provider", "gemini")  # the default backend
    elif tid == "browser":
        set_config_value(cfg_path, "browser.backend", "mcp")
        set_config_value(cfg_path, "browser.headless", True)
    elif tid == "mcp":
        set_config_value(cfg_path, "mcp.servers", [])
    out.print(f"[dim]{tid} reset to defaults[/]")


def _configure_image(ctx: typer.Context) -> None:
    """Pick the image backend: gemini/openai (reuse the model key) or a Cloudflare worker."""
    from gaia import constants
    from gaia.cli import setup  # _save_key lives with the other secret prompts
    from gaia.cli._envfile import get_env_var
    from gaia.cli._select import select_one
    from gaia.cli._yamledit import set_config_value
    from gaia.config import ConfigSupplier, get_settings

    out = console()
    settings = get_settings(state(ctx).env_file)
    cfg_path = settings.config_path
    env_path = state(ctx).env_file or constants.ENV_FILE
    img = ConfigSupplier(cfg_path).current.tools.get("generate_image")
    cur = getattr(img, "provider", None) or "gemini"

    prov = select_one(
        "Image provider",
        [
            (
                "gemini",
                "Google Imagen",
                "uses GEMINI_API_KEY",
                "current" if cur == "gemini" else "",
            ),
            (
                "openai",
                "OpenAI gpt-image-1",
                "uses OPENAI_API_KEY",
                "current" if cur == "openai" else "",
            ),
            (
                "cloudflare",
                "Cloudflare worker (SDXL)",
                "custom URL + token",
                "current" if cur == "cloudflare" else "",
            ),
        ],
        default=cur,
    )
    if prov is None:
        out.print("[yellow]cancelled[/]")
        raise typer.Exit(1)
    set_config_value(cfg_path, "tools.generate_image.provider", prov)

    if prov == "cloudflare":
        url = typer.prompt("Cloudflare worker URL").strip()
        if url:
            set_config_value(cfg_path, "tools.generate_image.cloudflare_url", url)
        existing = get_env_var(env_path, "GAIA_CLOUDFLARE_AI_TOKEN") or settings.cloudflare_ai_token
        setup._save_key(
            env_path,
            "GAIA_CLOUDFLARE_AI_TOKEN",
            existing=existing,
            flag=None,
            label="Cloudflare AI token",
        )
    else:
        out.print(f"[dim]image uses your {prov} API key — set it via `gaia model`[/]")
    out.print(f"image provider set to [bold]{prov}[/]")


def _manage_mcp(ctx: typer.Context) -> None:
    """MCP sub-picker: each server is a removable row; `+ add a server` runs the add flow."""
    from gaia.cli import setup
    from gaia.cli._select import select_manage
    from gaia.cli._yamledit import set_config_value
    from gaia.config import ConfigSupplier, get_settings

    cfg_path = get_settings(state(ctx).env_file).config_path
    servers = ConfigSupplier(cfg_path).current.mcp.servers

    rows: list[tuple[str, ...]] = [
        (s.name, f"🧩 {s.name}", s.transport, "configured") for s in servers
    ]
    rows.append(("__add__", "+ add a server", "name / transport / command", ""))
    chosen, removed = select_manage("Custom MCP servers", rows, marked=[s.name for s in servers])
    if "__add__" in chosen:
        setup.mcp(ctx)
    if removed:
        drop = set(removed)
        keep = [s.model_dump(exclude_defaults=True) for s in servers if s.name not in drop]
        set_config_value(cfg_path, "mcp.servers", keep)
        console().print(f"[yellow]removed:[/] {', '.join(removed)}")


def _enabled(cfg: GaiaConfig, tid: str) -> bool:
    entry = cfg.tools.get(tid)
    return entry.enabled if entry is not None else True  # tools are on unless disabled
