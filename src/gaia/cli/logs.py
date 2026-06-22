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
import re
import sys
import time
from collections import deque
from pathlib import Path
from typing import Annotated, Any

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

#: Actor tag for a stored line with no agent of its own (system files; matches the live default).
_DEFAULT_AGENT = "gaia"

#: A standard system/errors/daemon line: ``<date> <time>,<ms> <LEVEL> <name>: <message>``.
_SYS_RE = re.compile(
    r"^\S+ (?P<time>\d\d:\d\d:\d\d)\S* (?P<level>[A-Z]+) (?P<name>[\w.]+): (?P<msg>.*)$"
)


def tail_lines(path: Path, n: int) -> list[str]:
    """The last ``n`` lines of ``path`` (empty list when the file is missing)."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            return [line.rstrip("\n") for line in deque(fh, maxlen=n)]
    except FileNotFoundError:
        return []


class _Renderer:
    """gocat-style line renderer for the viewer — shares :mod:`gaia.logfmt` with the live console.

    Stateful only for the tag-dedup (``prev_tag``). ``--json`` (``raw``) bypasses rendering.
    """

    def __init__(self, *, events: bool, raw: bool) -> None:
        from gaia.logfmt import supports_color

        self._events = events
        self._raw = raw
        self._color = supports_color(sys.stdout)
        self._prev: str | None = None

    def render(self, line: str) -> str:
        if self._raw:
            return line
        return self._event(line) if self._events else self._system(line)

    def _emit(
        self, *, ts: str, tag: str, level: str, body: str, module: str, fields: Any, error: bool
    ) -> str:
        from gaia.logfmt import render_line

        out = render_line(
            ts=ts,
            tag=tag,
            level=level,
            body=body,
            module=module,
            fields=fields or None,
            color=self._color,
            prev_tag=self._prev,
            error=error,
        )
        self._prev = tag
        return out

    def _event(self, raw: str) -> str:
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw
        if not isinstance(obj, dict):
            return raw
        ts = str(obj.get("asctime", "")).split(" ")[-1].split(",")[0]  # HH:MM:SS
        action = str(obj.get("message", ""))  # the module (tool_used, message_in, …)
        agent = obj.get("agent") or _DEFAULT_AGENT
        project = obj.get("project")
        tag = f"{agent}/{project}" if project else str(agent)
        skip = {"asctime", "message", "levelname", "name", "taskName", "agent", "project"}
        fields = {k: str(v) for k, v in obj.items() if k not in skip}
        return self._emit(
            ts=ts,
            tag=tag,
            level=str(obj.get("levelname", "INFO")),
            body="",
            module=action,
            fields=fields,
            error=obj.get("status") == "error",
        )

    def _system(self, raw: str) -> str:
        m = _SYS_RE.match(raw)
        if m is None:
            return raw  # not a standard line (e.g. a wrapped traceback) — print verbatim
        # The stored text line doesn't carry the run's agent/project (only events.jsonl does), so
        # the actor tag is the root default; the logger name is the module.
        return self._emit(
            ts=m["time"],
            tag=_DEFAULT_AGENT,
            level=m["level"],
            body=m["msg"],
            module=m["name"].removeprefix("gaia."),
            fields=None,
            error=False,
        )


def _follow(path: Path, renderer: _Renderer) -> None:
    """Print appended lines until interrupted, reopening across rotation/truncation."""
    fh = path.open(encoding="utf-8", errors="replace")
    try:
        fh.seek(0, 2)  # start at EOF: the tail was already printed
        inode = path.stat().st_ino
        while True:
            line = fh.readline()
            if line:
                print(renderer.render(line.rstrip("\n")))
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

    renderer = _Renderer(events=events, raw=json_raw)
    for line in tail_lines(path, lines):
        print(renderer.render(line))
    if follow:
        try:
            _follow(path, renderer)
        except KeyboardInterrupt:
            raise typer.Exit(0) from None
