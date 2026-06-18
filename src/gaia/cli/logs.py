"""``gaia logs`` — view and follow the rotating log files under ``~/.gaia/logs``.

One file at a time: ``system.log`` (default), ``errors.log`` (``--errors``),
``daemon.log`` (``--daemon``) or ``events.jsonl`` (``--events``, pretty-rendered;
``--json`` prints the raw lines). ``-f/--follow`` tails like ``tail -F`` — a poll loop
that reopens the file on rotation (inode change) or truncation, so it survives the
``RotatingFileHandler`` rollover at ``logging.max_size_mb``.

Lazy-import rule (repo convention): only typer + stdlib (+ cli siblings) at module
level. The module name collides with ``gaia.logs`` on purpose (the issue allows it);
absolute imports keep them apart.
"""

from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Annotated

import typer

from gaia.cli._console import console
from gaia.cli._options import state

# Argument/option types named once so the command signature below stays readable.
FollowOpt = Annotated[
    bool, typer.Option("-f", "--follow", help="Follow the file (survives rotation).")
]
LinesOpt = Annotated[int, typer.Option("-n", "--lines", help="How many trailing lines to print.")]
ErrorsOpt = Annotated[bool, typer.Option("--errors", help="Show errors.log (WARNING+).")]
EventsOpt = Annotated[
    bool, typer.Option("--events", help="Show events.jsonl (structured activity).")
]
DaemonOpt = Annotated[bool, typer.Option("--daemon", help="Show daemon.log (start/serve).")]
JsonRawOpt = Annotated[
    bool, typer.Option("--json", help="With --events: print raw JSON lines, not pretty.")
]

#: Seconds between stat/read polls while following (``-f``).
_FOLLOW_POLL = 0.25


def tail_lines(path: Path, n: int) -> list[str]:
    """The last ``n`` lines of ``path`` (empty list when the file is missing)."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            return [line.rstrip("\n") for line in deque(fh, maxlen=n)]
    except FileNotFoundError:
        return []


def _format_event(raw: str) -> str:
    """Pretty-render one ``events.jsonl`` line as ``HH:MM:SS ▸ action  k=v …``.

    Mirrors the console event layout in :mod:`gaia.logs` without depending on a
    ``LogRecord``. Lines that are not JSON are returned verbatim (defensive).
    """
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
    if not isinstance(obj, dict):
        return raw
    ts = str(obj.get("asctime", "")).split(" ")[-1].split(",")[0]  # HH:MM:SS from asctime
    action = str(obj.get("message", ""))
    skip = {"asctime", "message", "levelname", "name", "taskName"}
    fields = " ".join(f"{k}={v}" for k, v in obj.items() if k not in skip)
    line = f"{ts} ▸ {action}".strip()
    return f"{line}  {fields}" if fields else line


def _render(line: str, *, events: bool, raw: bool) -> str:
    """Render one stored line for output: pretty events unless ``--json`` (raw)."""
    return _format_event(line) if events and not raw else line


def _follow(path: Path, *, events: bool, raw: bool) -> None:
    """Print appended lines until interrupted, reopening across rotation/truncation."""
    out = console()
    fh = path.open(encoding="utf-8", errors="replace")
    try:
        fh.seek(0, 2)  # start at EOF: the tail was already printed
        inode = path.stat().st_ino
        while True:
            line = fh.readline()
            if line:
                out.print(_render(line.rstrip("\n"), events=events, raw=raw))
                continue
            time.sleep(_FOLLOW_POLL)
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue  # mid-rollover gap; the new file shows up on a later poll
            # Rollover renames the file (new inode) or truncates it (size < position).
            if stat.st_ino != inode or stat.st_size < fh.tell():
                fh.close()
                fh = path.open(encoding="utf-8", errors="replace")
                inode = stat.st_ino
    finally:
        fh.close()


def logs(
    ctx: typer.Context,
    follow: FollowOpt = False,
    lines: LinesOpt = 50,
    errors: ErrorsOpt = False,
    events: EventsOpt = False,
    daemon: DaemonOpt = False,
    json_raw: JsonRawOpt = False,
) -> None:
    """Tail (and optionally follow) one of Gaia's log files."""
    from gaia.config import get_settings

    if errors + events + daemon > 1:
        raise typer.BadParameter("choose at most one of --errors / --events / --daemon")

    filename = (
        "errors.log"
        if errors
        else "events.jsonl"
        if events
        else "daemon.log"
        if daemon
        else "system.log"
    )
    path = get_settings(state(ctx).env_file).log_dir / filename
    out = console()
    if not path.exists():
        out.print(f"no log file at {path} yet — run 'gaia chat' or 'gaia start' to create logs")
        raise typer.Exit(1)

    for line in tail_lines(path, lines):
        out.print(_render(line, events=events, raw=json_raw))
    if follow:
        try:
            _follow(path, events=events, raw=json_raw)
        except KeyboardInterrupt:
            raise typer.Exit(0) from None
