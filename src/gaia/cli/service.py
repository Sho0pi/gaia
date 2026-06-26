"""``gaia service install|uninstall|status`` — run the daemon as an OS service.

Opt-in boot persistence + auto-restart on crash, wrapping ``gaia serve`` (``app.run_daemon``) under
the platform's service manager: **launchd** (macOS) or **systemd --user** (Linux). The service runs
``<python> -m gaia.cli serve``, where ``<python>`` is the installer's venv (``~/.gaia/venv``) when
present, else the current interpreter (dev/source runs).

Lazy-import rule (repo convention): typer + stdlib at module level.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

import typer

from gaia import constants
from gaia.cli._console import console

app = typer.Typer(
    name="service",
    help="Run gaia as a boot service (launchd on macOS, systemd --user on Linux).",
    no_args_is_help=True,
)

#: launchd label / systemd unit base name.
LABEL = "sh.gaia.daemon"
UNIT = "gaia.service"


def _python() -> str:
    """The interpreter the service runs — the installer's venv if present, else this one."""
    venv_py = constants.HOME_DIR / "venv" / "bin" / "python"
    return str(venv_py) if venv_py.exists() else sys.executable


def _is_mac() -> bool:
    return platform.system() == "Darwin"


def _is_linux() -> bool:
    return platform.system() == "Linux"


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / UNIT


def _plist_text() -> str:
    log = constants.LOG_DIR / "daemon.log"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        f"  <key>Label</key><string>{LABEL}</string>\n"
        "  <key>ProgramArguments</key>\n"
        f"  <array><string>{_python()}</string><string>-m</string>"
        "<string>gaia.cli</string><string>serve</string></array>\n"
        "  <key>RunAtLoad</key><true/>\n"
        # restart only on a non-zero (crash) exit — a clean `gaia stop` stays stopped.
        "  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>\n"
        f"  <key>StandardOutPath</key><string>{log}</string>\n"
        f"  <key>StandardErrorPath</key><string>{log}</string>\n"
        f"  <key>WorkingDirectory</key><string>{constants.HOME_DIR}</string>\n"
        "</dict>\n"
        "</plist>\n"
    )


def _unit_text() -> str:
    return (
        "[Unit]\n"
        "Description=gaia daemon\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        f"ExecStart={_python()} -m gaia.cli serve\n"
        "Restart=on-failure\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


@app.command()
def install() -> None:
    """Install + start the service (runs at login, restarts on crash)."""
    out = console()
    constants.LOG_DIR.mkdir(parents=True, exist_ok=True)
    if _is_mac():
        plist = _plist_path()
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(_plist_text())
        target = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", target, str(plist)], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", target, str(plist)], check=True)
        out.print(f"[green]service installed[/] → {plist}")
    elif _is_linux():
        unit = _unit_path()
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(_unit_text())
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", UNIT], check=True)
        out.print(f"[green]service installed[/] → {unit}")
        out.print("[dim]tip: run `loginctl enable-linger` to keep it up without an active login[/]")
    else:
        out.print("[red]no boot service on this OS[/] — use `gaia start`")
        raise typer.Exit(1)


@app.command()
def uninstall() -> None:
    """Stop + remove the service."""
    out = console()
    if _is_mac():
        plist = _plist_path()
        if plist.exists():
            subprocess.run(
                ["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], capture_output=True
            )
            plist.unlink(missing_ok=True)
        out.print("[green]service removed[/]")
    elif _is_linux():
        unit = _unit_path()
        subprocess.run(["systemctl", "--user", "disable", "--now", UNIT], capture_output=True)
        unit.unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        out.print("[green]service removed[/]")
    else:
        out.print("no service to remove")


@app.command()
def status() -> None:
    """Show the service state (defers to launchctl / systemctl)."""
    if _is_mac():
        if not _plist_path().exists():
            console().print("not installed — `gaia service install`")
            raise typer.Exit(1)
        subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"])
    elif _is_linux():
        subprocess.run(["systemctl", "--user", "status", UNIT, "--no-pager"])
    else:
        console().print("no boot service on this OS")
