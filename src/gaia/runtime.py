"""Provision gaia's non-Python runtime deps for whichever browser backend is active.

``install.sh`` sets these up on first install. ``gaia update`` only re-installs the Python package,
so a runtime drift (e.g. a playwright-mcp bump moving the Chromium revision → #303) would leave the
browser broken. :func:`ensure_runtime_deps` re-provisions so an update self-heals.

It provisions **only what the active backend needs** — the default ``native`` + ``camoufox`` engine
pulls just Camoufox's Firefox; bun + the playwright-mcp browser are fetched only if a user opts into
``backend: mcp``; ``native`` + ``chromium`` pulls Chromium. Idempotent + best-effort: each step
skips when already satisfied and never raises to the caller; it returns human-readable notes.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.config.schema import BrowserConfig

#: playwright-mcp's bundled-Playwright browser. Installed via its own ``install-browser`` (NOT
#: ``playwright install``, which fetches the standalone build at a different revision → #303).
_MCP_BROWSER = "chrome-for-testing"


def ensure_runtime_deps(venv_python: Path, browser_cfg: BrowserConfig) -> list[str]:
    """Install/repair the runtime deps for the active browser backend. Best-effort; returns notes.

    Only the selected backend's deps are provisioned: the default native+camoufox fetches just
    Camoufox's Firefox (~700MB, skipped when already installed); mcp pulls bun + the playwright-mcp
    browser; native+chromium pulls Chromium.
    """
    from gaia.mcp import resolve_browser_backend

    notes: list[str] = []

    if resolve_browser_backend(browser_cfg) == "mcp":
        bunx = _ensure_bun(notes)
        if bunx is not None:
            _run(
                [bunx, "@playwright/mcp@latest", "install-browser", _MCP_BROWSER],
                ok="playwright-mcp browser ready",
                fail="playwright-mcp browser install failed (screenshots may not work)",
                notes=notes,
            )
        return notes

    # native backend
    if browser_cfg.engine == "camoufox":
        # Fetch Camoufox's Firefox (~700MB), but only if it's not already there — re-fetching on
        # every update was a big chunk of the slowdown (and it's preinstalled on some hosts).
        if not _camoufox_installed(venv_python):
            _run(
                [str(venv_python), "-m", "camoufox", "fetch"],
                ok="camoufox browser ready",
                fail="camoufox fetch failed (arm64 may need a source build — see the browser docs)",
                notes=notes,
            )
        return notes

    playwright = venv_python.with_name("playwright")  # the venv's native Playwright CLI
    if playwright.exists():
        _run(
            [str(playwright), "install", "chromium"],
            ok="native chromium ready",
            fail="native chromium install failed",
            notes=notes,
        )
    return notes


def _camoufox_installed(venv_python: Path) -> bool:
    """True if the camoufox Firefox build is already fetched (lets us skip the ~700MB re-fetch)."""
    try:
        out = subprocess.run(
            [
                str(venv_python),
                "-c",
                "from camoufox.pkgman import installed_verstr; print(installed_verstr() or '')",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return out.returncode == 0 and bool(out.stdout.strip())


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
