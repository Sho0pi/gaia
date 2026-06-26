"""``gaia report`` — bundle the latest crash + recent logs + environment into a bug report.

Reads the redacted crash files (``gaia.crash``) + an ``errors.log`` tail + a small env summary,
shows them, and (on confirm) files a GitHub issue — via ``gh`` if available, else a prefilled
``issues/new`` URL the user reviews and submits. No secrets leave the machine without consent.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import tempfile
import webbrowser
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

import typer

from gaia.cli._console import console
from gaia.cli._options import state

REPO = "Sho0pi/gaia"
_ISSUE_NEW = f"https://github.com/{REPO}/issues/new"
_URL_MAX = 8000  # GitHub rejects very long prefilled URLs; spill to a file past this

AllOpt = Annotated[
    bool, typer.Option("--all", help="Include every crash report, not just the last.")
]
NoOpenOpt = Annotated[
    bool, typer.Option("--no-open", help="Print the URL instead of opening a browser.")
]


def report(ctx: typer.Context, show_all: AllOpt = False, no_open: NoOpenOpt = False) -> None:
    """Bundle the latest crash + recent logs + your environment into a GitHub bug report."""
    from gaia.config import get_settings
    from gaia.crash import recent_crashes

    out = console()
    settings = get_settings(state(ctx).env_file)
    crashes = recent_crashes()
    picked = crashes if show_all else crashes[-1:]
    title, body = _build(settings, picked)

    out.print(body)
    out.print()
    if not typer.confirm("File this as a GitHub issue?", default=True):
        out.print("[dim]not filed — the bundle above is yours to share.[/]")
        return
    _file(title, body, no_open=no_open)


def _build(settings: Any, crashes: list[Path]) -> tuple[str, str]:
    """Return (issue title, Markdown body) from the env + crash files + an errors.log tail."""
    from gaia import __version__, constants
    from gaia.config import ConfigSupplier
    from gaia.logs import _build_redactor

    redact = _build_redactor(settings)
    cfg = ConfigSupplier(settings.config_path).current
    conns = [n for n in ("telegram", "whatsapp", "cli") if getattr(cfg.connectors, n).enabled]
    parts = [
        "## Environment",
        f"- gaia `{__version__}`",
        f"- python {platform.python_version()} · {platform.system()} "
        f"{platform.release()} ({platform.machine()})",
        f"- model: `{cfg.llm.provider}/{cfg.llm.model}`",
        f"- connectors: {', '.join(conns) or 'none'}",
        "",
    ]
    if crashes:
        title = f"crash: {_signature(crashes[-1])}"
        for path in crashes:
            data = _read(path)
            parts += [
                f"## Crash — {data.get('time', path.stem)}",
                f"`{data.get('gaia_version', '?')}` · `{data.get('error', '')}`",
                "```",
                str(data.get("traceback", "")).strip(),
                "```",
                "",
            ]
    else:
        title = "bug: "
        parts += ["## What happened", "_(describe the bug — no crash report was found)_", ""]

    tail = _log_tail(constants.LOG_DIR / "errors.log", redact)
    if tail:
        parts += ["## Recent errors (`errors.log`)", "```", tail, "```"]
    return title, "\n".join(parts)


def _read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())  # type: ignore[no-any-return]
    except (OSError, json.JSONDecodeError):
        return {}


def _signature(crash_path: Path) -> str:
    return str(_read(crash_path).get("error", "unknown failure"))[:80]


def _log_tail(path: Path, redact: Any, lines: int = 40) -> str:
    try:
        return redact("\n".join(path.read_text().splitlines()[-lines:]))  # type: ignore[no-any-return]
    except OSError:
        return ""


def _file(title: str, body: str, *, no_open: bool) -> None:
    out = console()
    if shutil.which("gh"):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as fh:
            fh.write(body)
            body_file = fh.name
        done = subprocess.run(
            [
                "gh",
                "issue",
                "create",
                "--repo",
                REPO,
                "--label",
                "crash",
                "--title",
                title,
                "--body-file",
                body_file,
            ],
            capture_output=True,
            text=True,
        )
        if done.returncode == 0:
            out.print(f"[green]filed:[/] {done.stdout.strip()}")
            return
        out.print(f"[yellow]gh failed[/] ({done.stderr.strip()}) — falling back to a browser URL")

    url = f"{_ISSUE_NEW}?title={quote(title)}&labels=crash&body={quote(body)}"
    if len(url) > _URL_MAX:
        dump = Path(tempfile.gettempdir()) / "gaia-report.md"
        dump.write_text(body)
        out.print(f"[yellow]report is large[/] — saved to {dump}; open {_ISSUE_NEW} and paste it.")
        return
    if no_open:
        out.print(url)
        return
    out.print(f"[dim]opening a prefilled issue…[/] {_ISSUE_NEW}")
    try:
        webbrowser.open(url)
    except Exception:
        out.print(url)
