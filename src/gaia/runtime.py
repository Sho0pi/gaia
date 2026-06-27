"""Provision gaia's non-Python runtime deps: bun, the playwright-mcp browser, native chromium.

``install.sh`` sets these up on first install. ``gaia update`` only re-installs the Python package,
so a playwright-mcp version bump — which moves the Chromium revision its bundled Playwright wants —
would leave screenshots broken (#303). :func:`ensure_runtime_deps` re-provisions them so an update
self-heals, without re-running install.sh.

Idempotent + best-effort: each step skips when already satisfied and never raises to the caller; it
returns human-readable notes the CLI prints. Deps live in their default (global) locations for now;
moving them under ``~/.gaia`` + a Docker image is future work.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

#: playwright-mcp's bundled-Playwright browser. Installed via its own ``install-browser`` (NOT
#: ``playwright install``, which fetches the standalone build at a different revision → #303).
_MCP_BROWSER = "chrome-for-testing"


def ensure_runtime_deps(venv_python: Path, *, browser: bool = True) -> list[str]:
    """Install/repair the runtime deps. Idempotent, best-effort; returns status notes to print."""
    notes: list[str] = []
    if not browser:
        return notes

    bunx = _ensure_bun(notes)
    if bunx is not None:
        _run(
            [bunx, "@playwright/mcp@latest", "install-browser", _MCP_BROWSER],
            ok="playwright-mcp browser ready",
            fail="playwright-mcp browser install failed (screenshots may not work)",
            notes=notes,
        )

    playwright = venv_python.with_name("playwright")  # the venv's native Playwright CLI
    if playwright.exists():
        _run(
            [str(playwright), "install", "chromium"],
            ok="native chromium ready",
            fail="native chromium install failed",
            notes=notes,
        )
    return notes


def _ensure_bun(notes: list[str]) -> str | None:
    """Return a usable ``bunx`` path, installing bun first if it's missing."""
    from gaia.mcp import _resolve_runtime  # shared PATH + ~/.bun/bin discovery (#296)

    bunx = _resolve_runtime("bunx")
    if bunx is not None:
        return bunx
    notes.append("installing bun…")
    try:
        # The official bun installer (same as install.sh). Best-effort.
        subprocess.run(
            "curl -fsSL https://bun.sh/install | bash", shell=True, check=True, capture_output=True
        )
    except (subprocess.CalledProcessError, OSError):
        notes.append("bun install failed (the mcp browser backend won't be available)")
        return None
    return _resolve_runtime("bunx")


def _run(argv: list[str], *, ok: str, fail: str, notes: list[str]) -> None:
    """Run a provisioning command, recording a success/failure note (never raises)."""
    try:
        subprocess.run(argv, check=True, capture_output=True)
        notes.append(ok)
    except (subprocess.CalledProcessError, FileNotFoundError):
        notes.append(fail)
