"""``gaia doctor`` — an offline, read-only health report (openclaw-style).

Each check is a pure ``(DoctorContext) -> CheckResult`` function, so the report is a
plain list comprehension and every check is unit-testable by handing it a crafted
context. No network, no mutation: the pidfile check reads but never clears a stale
file, unlike :meth:`PidFile.read_live`.

Exit code: 0 when every check is OK/WARN, **4** (:data:`EXIT_DOCTOR`) on any FAIL.

Lazy-import rule (repo convention): only typer + stdlib (+ cli siblings) at module
level; the heavy ``gaia.app`` import (for ``plan_launch``) lives inside its check.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import typer

from gaia import constants
from gaia.cli._console import console, emit_json
from gaia.cli._options import state

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.config import GaiaConfig, Settings

#: Exit code when any check FAILs.
EXIT_DOCTOR = 4

Status = Literal["ok", "warn", "fail"]


@dataclass(frozen=True, slots=True)
class CheckResult:
    """The outcome of one doctor check: a status, a summary, and a fix hint."""

    status: Status
    message: str
    hint: str = ""


@dataclass(slots=True)
class DoctorContext:
    """Everything the checks read, gathered once so each check stays a pure function."""

    settings: Settings
    config: GaiaConfig | None
    config_error: str | None = None


def _check_home(ctx: DoctorContext) -> CheckResult:
    home = constants.HOME_DIR
    if not home.exists():
        return CheckResult("fail", f"{home} does not exist", f"mkdir -p {home}")
    if not os.access(home, os.W_OK):
        return CheckResult("fail", f"{home} is not writable", "fix the directory permissions")
    return CheckResult("ok", f"{home} exists and is writable")


def _check_config(ctx: DoctorContext) -> CheckResult:
    if ctx.config_error is not None:
        return CheckResult(
            "fail", f"gaia.yaml is invalid: {ctx.config_error}", f"fix {constants.CONFIG_PATH}"
        )
    if not constants.CONFIG_PATH.exists():
        return CheckResult("ok", "gaia.yaml absent — using built-in defaults")
    return CheckResult("ok", "gaia.yaml parses into GaiaConfig")


def _check_secrets(ctx: DoctorContext) -> CheckResult:
    if ctx.config is None:
        return CheckResult("warn", "config unavailable — skipping secret check")
    s, cfg = ctx.settings, ctx.config
    missing: list[str] = []
    provider = cfg.llm.provider.lower()
    if provider == "gemini" and not s.google_api_key:
        missing.append("GEMINI_API_KEY (llm.provider=gemini)")
    elif provider == "openai" and not s.openai_api_key:
        from gaia.providers.openai import load_credentials

        if load_credentials() is None:
            missing.append("OPENAI_API_KEY or ChatGPT login (run 'gaia llm auth openai')")
    if cfg.connectors.telegram.enabled and not s.telegram_bot_token:
        missing.append("GAIA_TELEGRAM_BOT_TOKEN (telegram enabled)")
    if cfg.connectors.whatsapp.enabled and not (
        s.has_whatsapp_business or s.whatsapp_session_db.exists()
    ):
        missing.append("WhatsApp creds or a paired session (whatsapp enabled)")
    if missing:
        return CheckResult(
            "fail", f"missing: {', '.join(missing)}", "set the credentials in ~/.gaia/.env"
        )
    return CheckResult("ok", "secrets present for the enabled features")


# Optional dep groups (pyproject.toml) keyed by an importable module they install.
_GROUP_MODULE = {"llm": "litellm", "memory": "chromadb", "mcp": "mcp", "browser": "playwright"}


def _check_optional_deps(ctx: DoctorContext) -> CheckResult:
    if ctx.config is None:
        return CheckResult("warn", "config unavailable — skipping dependency check")
    cfg = ctx.config
    needed: set[str] = set()
    if cfg.llm.provider.lower() != "gemini":
        needed.add("llm")
    if cfg.memory.enabled:
        needed.add("memory")
    if cfg.mcp.servers:
        needed.add("mcp")
    if cfg.browser.backend == "native":
        needed.add("browser")
    missing = [g for g in sorted(needed) if importlib.util.find_spec(_GROUP_MODULE[g]) is None]
    if missing:
        groups = ", ".join(missing)
        remedy = " && ".join(f"uv sync --group {g}" for g in missing)
        return CheckResult("fail", f"config needs dep group(s) not installed: {groups}", remedy)
    return CheckResult("ok", "optional dependency groups for the config are installed")


def _check_connector_combo(ctx: DoctorContext) -> CheckResult:
    if ctx.config is None:
        return CheckResult("warn", "config unavailable — skipping connector check")
    from gaia.app import plan_launch  # heavy (pulls ADK); lazy per cli convention

    try:
        selected = plan_launch(ctx.config)
    except ValueError as exc:
        return CheckResult("fail", str(exc), "adjust the enabled connectors in gaia.yaml")
    return CheckResult("ok", f"connector combo valid: {', '.join(selected) or 'none enabled'}")


def _check_souls(ctx: DoctorContext) -> CheckResult:
    from gaia.agents.spec import AgentSpec

    registry = constants.AGENT_REGISTRY_DIR
    if not registry.exists():
        return CheckResult("ok", "no soul registry yet (nothing to validate)")
    bad: list[str] = []
    count = 0
    for path in sorted(registry.glob("*.json")):
        count += 1
        try:
            AgentSpec.model_validate_json(path.read_text())
        except (ValueError, OSError):
            bad.append(path.name)
    if bad:
        return CheckResult(
            "fail", f"unparseable soul JSON: {', '.join(bad)}", "fix or delete the files"
        )
    return CheckResult("ok", f"{count} soul(s) parse as AgentSpec")


def _check_pidfile(ctx: DoctorContext) -> CheckResult:
    from gaia.cli._pidfile import PidFile

    pidfile = PidFile()
    pid = pidfile.read()
    if pid is None:
        return CheckResult("ok", "no daemon pidfile (not running)")
    if pidfile.alive(pid):
        return CheckResult("ok", f"daemon pidfile points at a live process (pid {pid})")
    return CheckResult("warn", f"stale pidfile (pid {pid} is dead)", "run 'gaia stop' to clear it")


def _check_log_dir(ctx: DoctorContext) -> CheckResult:
    log_dir = Path(ctx.settings.log_dir)
    if log_dir.exists():
        if os.access(log_dir, os.W_OK):
            return CheckResult("ok", f"{log_dir} is writable")
        return CheckResult("fail", f"{log_dir} is not writable", "fix the directory permissions")
    parent = log_dir.parent
    if os.access(parent, os.W_OK):
        return CheckResult("ok", f"{log_dir} absent but creatable")
    return CheckResult("fail", f"cannot create {log_dir}", f"fix permissions on {parent}")


#: The report, in display order. Each entry is unit-testable in isolation.
CHECKS: list[tuple[str, Callable[[DoctorContext], CheckResult]]] = [
    ("home", _check_home),
    ("config", _check_config),
    ("secrets", _check_secrets),
    ("dependencies", _check_optional_deps),
    ("connectors", _check_connector_combo),
    ("souls", _check_souls),
    ("pidfile", _check_pidfile),
    ("log_dir", _check_log_dir),
]

_STATUS_COLOR = {"ok": "green", "warn": "yellow", "fail": "red"}


def _build_context(env_file: Path | None) -> DoctorContext:
    """Gather settings + a freshly-parsed config (capturing parse errors for check #2)."""
    import yaml
    from pydantic import ValidationError

    from gaia.config import GaiaConfig, get_settings

    settings = get_settings(env_file)
    config: GaiaConfig | None = None
    config_error: str | None = None
    try:
        raw: object = {}
        if constants.CONFIG_PATH.exists():
            raw = yaml.safe_load(constants.CONFIG_PATH.read_text()) or {}
        config = GaiaConfig.model_validate(raw if isinstance(raw, dict) else {})
    except (yaml.YAMLError, ValidationError) as exc:
        config_error = str(exc).splitlines()[0]
    return DoctorContext(settings=settings, config=config, config_error=config_error)


def doctor(ctx: typer.Context) -> None:
    """Run offline health checks and report OK/WARN/FAIL per item (exit 4 on any FAIL)."""
    st = state(ctx)
    context = _build_context(st.env_file)
    results = [(name, fn(context)) for name, fn in CHECKS]
    failed = any(r.status == "fail" for _, r in results)

    if st.json:
        emit_json(
            {
                "ok": not failed,
                "checks": [
                    {"name": name, "status": r.status, "message": r.message, "hint": r.hint}
                    for name, r in results
                ],
            }
        )
    else:
        out = console()
        for name, r in results:
            tag = f"[{_STATUS_COLOR[r.status]}]{r.status.upper():<4}[/]"
            out.print(f"{tag} {name} — {r.message}")
            if r.hint and r.status != "ok":
                out.print(f"       ↳ {r.hint}")
    raise typer.Exit(EXIT_DOCTOR if failed else 0)
