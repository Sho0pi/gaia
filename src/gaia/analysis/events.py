"""Read ``events.jsonl`` and reduce it to a compact, model-ready digest.

The reduction happens **in code**: counts, frequencies and recurring tool sequences are
computed here, and only :func:`render_digest`'s few dozen lines ever reach the LLM —
never raw events (the #18 extractor principle, without the SQLite store). The files are
bounded by log rotation (``logging.max_size_mb`` x ``backup_count``), so reading them
into memory is fine.

Event shape (see :func:`gaia.logs.log_event` and ``core/plugins.py``): one JSON object
per line with ``asctime``/``message`` (the action) plus structured extras — ``user``,
``session``, ``tool``, ``agent``, ``status``, ``command``, ``error`` …
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

#: The events file (and its RotatingFileHandler backups ``events.jsonl.1..N``).
EVENTS_FILE = "events.jsonl"

#: ``asctime`` format written by the logging formatter.
_ASCTIME = "%Y-%m-%d %H:%M:%S,%f"

#: How many of each top-N list the digest keeps (tools, sequences, commands).
TOP_N = 10


class EventDigest(BaseModel):
    """The compact aggregation of an event window — all the LLM will ever see."""

    since: str
    until: str
    total_events: int
    users: dict[str, int] = Field(default_factory=dict, description="user -> turns (message_in)")
    messages_out: int = 0
    errors: dict[str, int] = Field(default_factory=dict, description="error type -> count")
    tools: dict[str, int] = Field(default_factory=dict, description="'tool (status)' -> calls")
    commands: dict[str, int] = Field(default_factory=dict, description="slash command -> uses")
    tool_sequences: dict[str, int] = Field(
        default_factory=dict, description="'a -> b' / 'a -> b -> c' within one session -> count"
    )
    active_days: int = 0


def _parse_time(raw: Any) -> datetime | None:
    """Parse the formatter's ``asctime`` (``2026-06-12 09:30:00,123``); None if malformed."""
    if not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw, _ASCTIME)
    except ValueError:
        return None


def read_events(log_dir: Path, since: datetime) -> list[dict[str, Any]]:
    """Events from ``events.jsonl`` (+ rotated backups, oldest first) at/after ``since``.

    Malformed lines and events without a parsable timestamp are skipped — the log is
    best-effort input, never a reason to fail the analysis.
    """
    log_dir = Path(log_dir)
    paths = [
        *sorted(
            log_dir.glob(f"{EVENTS_FILE}.*"),
            key=lambda p: int(p.suffix[1:]) if p.suffix[1:].isdigit() else 0,
            reverse=True,  # events.jsonl.5 (oldest) first, .1 last
        ),
        log_dir / EVENTS_FILE,
    ]

    events: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            ts = _parse_time(obj.get("asctime"))
            if ts is None or ts < since:
                continue
            obj["_ts"] = ts  # parsed once here; digest reads it and drops it
            events.append(obj)
    return events


def _sequences(tools_by_session: dict[str, list[str]]) -> Counter[str]:
    """Count tool bigrams/trigrams within each session — the recurring-pattern signal."""
    counts: Counter[str] = Counter()
    for tools in tools_by_session.values():
        for n in (2, 3):
            for i in range(len(tools) - n + 1):
                counts[" -> ".join(tools[i : i + n])] += 1
    return counts


def digest_events(events: list[dict[str, Any]]) -> EventDigest:
    """Aggregate raw events into an :class:`EventDigest` (pure, deterministic)."""
    users: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    tools: Counter[str] = Counter()
    commands: Counter[str] = Counter()
    tools_by_session: dict[str, list[str]] = {}
    days: set[str] = set()
    messages_out = 0
    times = [e["_ts"] for e in events]

    for event in events:
        action = event.get("message")
        days.add(event["_ts"].strftime("%Y-%m-%d"))
        if action == "message_in":
            users[str(event.get("user", "?"))] += 1
        elif action == "message_out":
            messages_out += 1
        elif action == "turn_error":
            errors[str(event.get("error", "?"))] += 1
        elif action == "tool_used":
            tool = str(event.get("tool", "?"))
            tools[f"{tool} ({event.get('status', '?')})"] += 1
            # Sequences only over one conversation; fall back to the agent as the bucket.
            bucket = str(event.get("session") or event.get("agent") or "?")
            tools_by_session.setdefault(bucket, []).append(tool)
        elif action == "command_used":
            commands[str(event.get("command", "?"))] += 1

    sequences = _sequences(tools_by_session)
    return EventDigest(
        since=min(times).isoformat(sep=" ") if times else "",
        until=max(times).isoformat(sep=" ") if times else "",
        total_events=len(events),
        users=dict(users.most_common()),
        messages_out=messages_out,
        errors=dict(errors.most_common(TOP_N)),
        tools=dict(tools.most_common(TOP_N)),
        commands=dict(commands.most_common(TOP_N)),
        tool_sequences={seq: n for seq, n in sequences.most_common(TOP_N) if n >= 2},
        active_days=len(days),
    )


def render_digest(digest: EventDigest) -> str:
    """Render the digest as the compact text block handed to the analyst."""

    def block(title: str, counts: dict[str, int]) -> str:
        if not counts:
            return f"{title}: none"
        lines = "\n".join(f"  {k}: {v}" for k, v in counts.items())
        return f"{title}:\n{lines}"

    return "\n".join(
        [
            f"window: {digest.since} .. {digest.until} "
            f"({digest.total_events} events, {digest.active_days} active day(s))",
            block("turns per user (message_in)", digest.users),
            f"messages out: {digest.messages_out}",
            block("tool calls (status)", digest.tools),
            block("recurring tool sequences (within a session)", digest.tool_sequences),
            block("slash commands", digest.commands),
            block("turn errors", digest.errors),
        ]
    )
