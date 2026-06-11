"""Daemon lifecycle commands: serve, start, stop, restart, status.

Spawn-self + pidfile — ``start`` execs a fresh interpreter (``python -m godpy.cli
serve``) detached via ``start_new_session``; no fork-based daemonization (``fork()``
without ``exec`` is unsafe on macOS and with the native-threaded deps godpy loads).
``start``/``stop``/``status`` import only light config modules — never ``godpy.app``
(which pulls ADK) — so they stay fast.

Exit code 3 (:data:`EXIT_DAEMON`) = daemon-state errors: already running / not running.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Annotated

import typer

from godpy.cli import _pidfile
from godpy.cli._console import console, emit_json
from godpy.cli._options import CliState, state

#: Exit code for daemon-state errors (already running / not running).
EXIT_DAEMON = 3
#: Seconds `start` waits for the child's pidfile before reporting "not confirmed".
_START_WAIT = 5.0
#: Poll interval for start/stop wait loops.
_POLL = 0.1


def serve(
    ctx: typer.Context,
    hold: Annotated[
        bool,
        typer.Option(
            "--hold",
            hidden=True,
            help="Keep running with zero connectors (testing / service debugging).",
        ),
    ] = False,
) -> None:
    """Run the background connectors in the foreground (what the daemon executes)."""
    pid = _pidfile.read_live()
    if pid is not None:
        console().print(f"already running (pid {pid}) — use 'godpy stop' first")
        raise typer.Exit(EXIT_DAEMON)
    from godpy.app import run_daemon

    raise typer.Exit(run_daemon(env_file=state(ctx).env_file, hold=hold))


def start(ctx: typer.Context) -> None:
    """Start the daemon in the background (see 'godpy status' / 'godpy stop')."""
    raise typer.Exit(_start(state(ctx)))


def stop(
    ctx: typer.Context,
    timeout: Annotated[
        int, typer.Option(help="Seconds to wait for graceful shutdown before SIGKILL.")
    ] = 10,
) -> None:
    """Stop the running daemon (SIGTERM, then SIGKILL after --timeout)."""
    raise typer.Exit(_stop(timeout))


def restart(
    ctx: typer.Context,
    timeout: Annotated[
        int, typer.Option(help="Seconds to wait for graceful shutdown before SIGKILL.")
    ] = 10,
) -> None:
    """Restart the daemon (stop if running, then start)."""
    if _stop(timeout) == EXIT_DAEMON:
        console().print("was not running")
    raise typer.Exit(_start(state(ctx)))


def status(ctx: typer.Context) -> None:
    """Show daemon state: pid, uptime, connectors, model, log paths."""
    from godpy.config import BACKGROUND_CONNECTORS, ConfigSupplier, get_settings

    st = state(ctx)
    pid = _pidfile.read_live()  # also cleans a stale pidfile
    settings = get_settings(st.env_file)
    cfg = ConfigSupplier(settings.config_path).current
    uptime = int(time.time() - _pidfile.PID_FILE.stat().st_mtime) if pid is not None else None
    connectors = [name for name in BACKGROUND_CONNECTORS if getattr(cfg.connectors, name).enabled]
    data = {
        "running": pid is not None,
        "pid": pid,
        "uptime_seconds": uptime,
        "connectors": connectors,
        "llm": {"provider": cfg.llm.provider, "model": cfg.llm.model},
        "logs": {
            "daemon": str(settings.log_dir / "daemon.log"),
            "system": str(settings.log_dir / "system.log"),
            "errors": str(settings.log_dir / "errors.log"),
            "events": str(settings.log_dir / "events.jsonl"),
        },
        "pidfile": str(_pidfile.PID_FILE),
    }
    if st.json:
        emit_json(data)
    else:
        out = console()
        if pid is not None and uptime is not None:
            out.print(f"running (pid {pid}, up {_fmt_duration(uptime)})")
        else:
            out.print("not running")
        out.print(f"connectors: {', '.join(connectors) or 'none enabled'}")
        out.print(f"llm: {cfg.llm.provider} / {cfg.llm.model or 'default'}")
        out.print(f"logs: {settings.log_dir}")
    raise typer.Exit(0 if pid is not None else EXIT_DAEMON)


def _start(st: CliState) -> int:
    """Spawn the detached serve process; poll for its pidfile or early death."""
    from godpy.config import BACKGROUND_CONNECTORS, ConfigSupplier, get_settings

    pid = _pidfile.read_live()
    if pid is not None:
        console().print(f"already running (pid {pid})")
        return EXIT_DAEMON

    # Fail fast in the parent (light config read; serve re-checks itself).
    settings = get_settings(st.env_file)
    cfg = ConfigSupplier(settings.config_path).current
    enabled = [name for name in BACKGROUND_CONNECTORS if getattr(cfg.connectors, name).enabled]
    if not enabled:
        console().print(
            "no background channels enabled in god.yaml — enable connectors.telegram "
            "or connectors.whatsapp first"
        )
        return 1

    argv = [sys.executable, "-m", "godpy.cli"]
    if st.env_file is not None:
        argv += ["--env-file", str(st.env_file)]  # global flag: precedes the subcommand
    argv.append("serve")
    log_path = settings.log_dir / "daemon.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Append: keep the previous run's crash evidence (structured logs rotate separately).
    with log_path.open("ab") as log:
        proc = subprocess.Popen(
            argv,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # detach: survives the parent shell exiting
        )

    deadline = time.monotonic() + _START_WAIT
    while time.monotonic() < deadline:
        if proc.poll() is not None:  # death check first: a crash also leaves no pidfile
            console().print(
                f"daemon exited immediately (code {proc.returncode}) — last lines of {log_path}:"
            )
            for line in _tail(log_path, 15):
                console().print(f"  {line}")
            return 1
        if _pidfile.read() == proc.pid:  # serve writes its pidfile once startup committed
            console().print(f"started (pid {proc.pid}) — logs: {log_path}")
            return 0
        time.sleep(_POLL)
    console().print(f"starting (pid {proc.pid}) — startup not confirmed yet, check 'godpy status'")
    return 0


def _stop(timeout: int) -> int:
    """SIGTERM the daemon, wait up to ``timeout``, SIGKILL as a last resort."""
    pid = _pidfile.read_live()  # stale file auto-removed here
    if pid is None:
        console().print("not running")
        return EXIT_DAEMON
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pidfile.alive(pid):
            _pidfile.remove()
            console().print(f"stopped (pid {pid})")
            return 0
        time.sleep(_POLL)
    console().print(f"[yellow]pid {pid} did not exit within {timeout}s — sending SIGKILL[/]")
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:  # died between the poll and the kill
        pass
    _pidfile.remove()  # the killed child cannot remove its own file
    return 0


def _tail(path: Path, lines: int) -> list[str]:
    """The last ``lines`` lines of ``path`` (empty when the file is missing)."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            return [line.rstrip("\n") for line in deque(fh, maxlen=lines)]
    except FileNotFoundError:
        return []


def _fmt_duration(seconds: int) -> str:
    """Compact human duration: '42s', '3m 12s', '2h 13m', '4d 6h'."""
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def register(app: typer.Typer) -> None:
    """Attach the daemon commands as flat (top-level) commands on ``app``."""
    for command in (serve, start, stop, restart, status):
        app.command()(command)
