"""``gaia completion`` — install or print shell tab-completion for the gaia CLI.

Wraps Typer's own completion machinery (the same code behind ``--install-completion``), so one
command gives advanced tab-completion: commands, options, and dynamic values (config keys, soul
keys, task ids, … — see :mod:`gaia.cli._complete`). Also offered during ``gaia setup``.

Lazy-import rule (repo convention): only typer + stdlib (+ cli siblings) at module level.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from gaia.cli._console import console

app = typer.Typer(
    name="completion", help="Install shell tab-completion for gaia.", no_args_is_help=True
)

_PROG = "gaia"
_COMPLETE_VAR = "_GAIA_COMPLETE"

ShellOpt = Annotated[
    str | None,
    typer.Option("--shell", help="Target shell (bash/zsh/fish/powershell); default: auto-detect."),
]


def run_install(shell: str | None = None) -> tuple[str, Path]:
    """Install completion into the shell config; returns ``(shell, path)``. Reused by setup."""
    from typer._completion_shared import install as typer_install

    return typer_install(shell=shell, prog_name=_PROG)


def _script_paths() -> list[Path]:
    """Where Typer writes the completion script per shell (mirrors typer._completion_shared)."""
    home = Path.home()
    return [
        home / ".bash_completions" / f"{_PROG}.sh",
        home / ".zfunc" / f"_{_PROG}",
        home / ".config" / "fish" / "completions" / f"{_PROG}.fish",
    ]


def run_uninstall() -> list[Path]:
    """Remove installed completion scripts (all shells, best-effort); returns the ones removed.

    Reused by ``gaia uninstall``. Also strips the ``source …gaia.sh`` line Typer adds to ``.bashrc``
    so the now-missing script can't error on shell start.
    """
    removed: list[Path] = []
    for p in _script_paths():
        try:
            p.unlink()
            removed.append(p)
        except OSError:
            pass  # not installed for this shell — fine
    bash_script = Path.home() / ".bash_completions" / f"{_PROG}.sh"
    _strip_rc_lines(Path.home() / ".bashrc", f"source '{bash_script}'")
    return removed


def _strip_rc_lines(rc: Path, needle: str) -> None:
    """Remove any line containing ``needle`` from ``rc`` (best-effort; keeps every other line)."""
    try:
        lines = rc.read_text().splitlines(keepends=True)
    except OSError:
        return
    kept = [ln for ln in lines if needle not in ln]
    if len(kept) != len(lines):
        rc.write_text("".join(kept))


@app.command()
def install(shell: ShellOpt = None) -> None:
    """Install tab-completion into your shell config (restart the shell to load it)."""
    installed_shell, path = run_install(shell)
    console().print(f"[green]✓[/] {installed_shell} completion written to [bold]{path}[/]")
    console().print("restart your shell (or source that file) to load it.")


@app.command()
def uninstall() -> None:
    """Remove gaia's shell completion (all shells)."""
    removed = run_uninstall()
    if removed:
        for p in removed:
            console().print(f"[green]✓[/] removed [bold]{p}[/]")
    else:
        console().print("no completion scripts found.")


@app.command()
def show(shell: ShellOpt = None) -> None:
    """Print the completion script (to inspect it or install it by hand)."""
    from typer._completion_shared import _get_shell_name, get_completion_script

    target = shell or _get_shell_name()
    if not target:
        console().print("[red]could not detect your shell — pass --shell[/]")
        raise typer.Exit(1)
    console().print(
        get_completion_script(prog_name=_PROG, complete_var=_COMPLETE_VAR, shell=target),
        soft_wrap=True,
    )
