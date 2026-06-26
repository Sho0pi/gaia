"""``gaia tools`` — one home for tool configuration.

A gaia-themed multi-select of the tools you can configure (emoji + one-line brief), matching the
model/connector pickers. **space** = (re)configure, **backspace** = reset to defaults, **enter** =
apply. ``--all`` switches to an enable/disable manager for the optional on/off tools. The
``gaia setup`` wizard's "Tools" step calls :func:`tools`.

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
    """Configure tools: browser, web search, custom MCP servers (and `--all` to toggle the rest)."""
    from gaia.config import ConfigSupplier, get_settings

    cfg_path = get_settings(state(ctx).env_file).config_path
    cfg = ConfigSupplier(cfg_path).current

    if show_all:
        _toggle_optional(cfg, cfg_path)
        return

    from gaia.cli._select import select_manage

    rows = [
        (tid, f"{emoji} {label}", brief, "configured" if _configured(cfg, tid) else "")
        for tid, (emoji, label, brief) in _CONFIGURABLE.items()
    ]
    marked = [tid for tid in _CONFIGURABLE if _configured(cfg, tid)]
    to_setup, to_reset = select_manage("Tools", rows, marked=marked)
    for tid in to_setup:
        _configure(tid, ctx)
    for tid in to_reset:
        _reset(tid, cfg_path)


def _configured(cfg: GaiaConfig, tid: str) -> bool:
    """Whether a configurable tool has real settings (so it shows the `―` 'configured' marker)."""
    if tid == "web_search":
        ws = cfg.tools.get("web_search")
        return bool(ws and getattr(ws, "engine", None))
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
    elif tid == "mcp":
        _manage_mcp(ctx)


def _reset(tid: str, cfg_path: Path) -> None:
    """Reset a tool's settings to gaia's defaults (comment-preserving)."""
    from gaia.cli._yamledit import set_config_value

    out = console()
    if tid == "web_search":
        set_config_value(cfg_path, "tools.web_search.engine", "duckduckgo")  # the no-key default
    elif tid == "browser":
        set_config_value(cfg_path, "browser.backend", "mcp")
        set_config_value(cfg_path, "browser.headless", True)
    elif tid == "mcp":
        set_config_value(cfg_path, "mcp.servers", [])
    out.print(f"[dim]{tid} reset to defaults[/]")


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


def _toggle_optional(cfg: GaiaConfig, cfg_path: Path) -> None:
    """`--all`: enable/disable the optional on/off tools (space toggles, enter persists)."""
    from gaia.cli._select import select_many
    from gaia.cli._yamledit import set_config_value

    rows = [(tid, f"{emoji} {label}", brief) for tid, (emoji, label, brief) in _TOGGLEABLE.items()]
    enabled = [tid for tid in _TOGGLEABLE if _enabled(cfg, tid)]
    chosen = set(select_many("Enable tools", rows, selected=enabled, marked=enabled))
    for tid in _TOGGLEABLE:
        want = tid in chosen
        if want != (tid in enabled):  # only write what changed
            set_config_value(cfg_path, f"tools.{tid}.enabled", want)
            console().print(f"{'enabled' if want else 'disabled'} [bold]{tid}[/]")
