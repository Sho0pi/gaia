"""``gaia update`` / ``gaia uninstall`` — manage the install (the venv at ``~/.gaia/venv``).

The installer (``install.sh``) puts gaia in a self-contained venv via
``uv pip install "gaia[all] @ git+…"`` and links a ``gaia`` shim into ``~/.local/bin``. These
commands wrap the upgrade + removal so a user never has to remember the uv invocations.

Lazy-import rule (repo convention): typer + stdlib (+ cli siblings) at module level.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from gaia import constants
from gaia.cli._console import console

#: Where the installer pulls gaia from (matches install.sh).
REPO = "https://github.com/Sho0pi/gaia"


def _venv() -> Path:
    return constants.HOME_DIR / "venv"


def _shim() -> Path:
    return Path.home() / ".local" / "bin" / "gaia"


def _gaia_cmd() -> str:
    """The installed `gaia` entry point (the shim, else whatever's on PATH)."""
    shim = _shim()
    return str(shim) if shim.exists() else "gaia"


RefOpt = Annotated[str | None, typer.Option("--ref", help="git ref to install (branch/tag/sha).")]
ExtrasOpt = Annotated[str, typer.Option("--extras", help="Extras to install (default: all).")]


def _latest_release_tag() -> str | None:
    """The newest published release tag (incl. prereleases), or None. Mirrors install.sh: uses the
    releases *list*, not ``/releases/latest`` (which skips prereleases like our alpha). Best-effort
    over stdlib urllib (no dep) — returns None on any failure so the caller falls back to master.
    """
    import json
    import urllib.request

    slug = REPO.removeprefix("https://github.com/")
    url = f"https://api.github.com/repos/{slug}/releases?per_page=1"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.load(resp)
        return data[0]["tag_name"] if data else None
    except Exception:
        return None


def update(ref: RefOpt = None, extras: ExtrasOpt = "all") -> None:
    """Upgrade gaia in place (re-pull from git); restart the daemon if it's running.

    With no ``--ref`` it installs the latest release (matching install.sh); pass ``--ref main`` for
    bleeding-edge HEAD. Falls back to master if no release is found.
    """
    from gaia.cli._pidfile import PidFile

    out = console()
    venv = _venv()
    if not venv.exists():
        out.print(f"[red]no gaia venv at {venv}[/] — (re)install with install.sh")
        raise typer.Exit(1)

    if ref is None:
        ref = _latest_release_tag()  # default to the latest release, not master HEAD
    spec = f"gaia[{extras}] @ git+{REPO}" + (f"@{ref}" if ref else "")
    out.print(f"updating gaia from [dim]{spec}[/] …")
    try:
        subprocess.run(
            ["uv", "pip", "install", "--python", str(venv), "--upgrade", "--reinstall", spec],
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        out.print(f"[red]update failed[/]: {exc}")
        raise typer.Exit(1) from exc

    after = subprocess.run(
        [str(venv / "bin" / "gaia"), "--version"], capture_output=True, text=True
    )
    out.print(f"[green]updated[/] — {(after.stdout or '').strip() or 'gaia'}")

    # Repair the runtime deps too — `uv pip install` only touches the Python package, so a
    # playwright-mcp bump (which moves the browser revision) would otherwise leave screenshots
    # broken until the next install.sh run (#303).
    from gaia.runtime import ensure_runtime_deps

    for note in ensure_runtime_deps(venv / "bin" / "python"):
        out.print(f"[dim]{note}[/]")

    if PidFile().read_live() is not None:  # the daemon is up → reload the new code
        out.print("restarting the daemon to apply…")
        subprocess.run([_gaia_cmd(), "restart"])


PurgeOpt = Annotated[bool, typer.Option("--purge", help="Also delete ~/.gaia (non-interactive).")]
KeepOpt = Annotated[bool, typer.Option("--keep", help="Keep ~/.gaia (non-interactive).")]


def uninstall(purge: PurgeOpt = False, keep: KeepOpt = False) -> None:
    """Remove gaia. Asks before deleting ~/.gaia unless --purge/--keep is given."""
    out = console()
    venv, shim, home = _venv(), _shim(), constants.HOME_DIR

    if not typer.confirm("Remove gaia (the program + boot service)?", default=True):
        raise typer.Exit(0)

    # Stop the daemon + remove the boot service first (best-effort; no-ops if not present).
    subprocess.run([_gaia_cmd(), "stop"], capture_output=True)
    subprocess.run([_gaia_cmd(), "service", "uninstall"], capture_output=True)

    remove_data = purge
    if not purge and not keep:
        remove_data = typer.confirm(
            f"Also delete {home} (config, memory, users, logs)?", default=False
        )

    shim.unlink(missing_ok=True)  # the shim isn't in the venv, so it's safe to remove now
    # The venv (and data) hold this running interpreter — defer their removal until we exit.
    _detached_rm([str(home)] if remove_data else [str(venv)])

    tail = "" if remove_data else f" Your data stays in {home}."
    out.print(f"[green]gaia removed.[/]{tail}")


def _detached_rm(paths: list[str]) -> None:
    """Spawn a detached cleanup that waits for THIS process to exit, then ``rm -rf`` the paths."""
    quoted = " ".join(f"'{p}'" for p in paths)
    script = f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.2; done; rm -rf {quoted}"
    subprocess.Popen(["sh", "-c", script], start_new_session=True)
