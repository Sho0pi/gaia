"""Reduce the error events in ``events.jsonl`` to a compact digest for the health analyst.

Same reduce-in-code rule as :mod:`gaia.analysis.events`: group + count here, only
:func:`render_error_digest`'s few lines reach the LLM — never raw events. Error signal lives in
three event kinds (see :func:`gaia.logs.log_event` / :func:`gaia.logs.log_error`):

- ``turn_error`` ``{error, detail, where, user}`` — a conversation turn blew up
- ``tool_used`` ``{tool, status: "error", error, detail}`` — a tool raised
- ``error`` ``{source, error, detail, where}`` — a background failure (loop/scheduler/daemon)

A group's **signature** is ``"ErrorType @ location"`` (location = the gaia frame, tool, or source),
stable across occurrences so the loop can dedup on it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, Field

#: How many error groups (top by count) the digest keeps.
TOP_N = 15


class ErrorGroup(BaseModel):
    """One error signature aggregated over the window."""

    signature: str
    error: str
    location: str
    count: int
    kinds: dict[str, int] = Field(default_factory=dict, description="event kind -> count")
    sample: str = Field(default="", description="one truncated message")
    first_seen: str = ""
    last_seen: str = ""


class ErrorDigest(BaseModel):
    """The compact aggregation of an error window — all the analyst sees."""

    since: str
    until: str
    total_errors: int
    groups: list[ErrorGroup] = Field(default_factory=list)


def _error_rows(events: list[dict[str, Any]]) -> Iterator[tuple[dict[str, Any], str, str]]:
    """Yield ``(event, error_type, location)`` for each error-bearing event.

    The monitor's OWN failures (``source`` = ``monitor_*``) are skipped — it must not report on or
    file issues about itself (a meta-loop; e.g. a flaky analyst JSON parse), only the rest of gaia.
    """
    for event in events:
        action = event.get("message")
        if action == "turn_error":
            yield event, str(event.get("error", "?")), str(event.get("where") or "?")
        elif action == "error":
            if str(event.get("source") or "").startswith("monitor"):
                continue  # don't report the monitor's own loop/scheduler errors
            yield (
                event,
                str(event.get("error", "?")),
                str(event.get("where") or event.get("source") or "?"),
            )
        elif action == "tool_used" and event.get("status") == "error":
            yield event, str(event.get("error", "?")), str(event.get("tool") or "?")


def error_digest(events: list[dict[str, Any]]) -> ErrorDigest:
    """Aggregate error events into an :class:`ErrorDigest` (pure, deterministic)."""
    counts: Counter[str] = Counter()
    kinds: dict[str, Counter[str]] = {}
    meta: dict[str, dict[str, Any]] = {}
    times: list[Any] = []

    for event, error, location in _error_rows(events):
        sig = f"{error} @ {location}"
        counts[sig] += 1
        kinds.setdefault(sig, Counter())[str(event.get("message"))] += 1
        ts = event.get("_ts")
        if ts is not None:
            times.append(ts)
        m = meta.setdefault(sig, {"error": error, "location": location, "sample": "", "ts": []})
        if not m["sample"] and event.get("detail"):
            m["sample"] = str(event.get("detail"))
        if ts is not None:
            m["ts"].append(ts)

    groups = [
        ErrorGroup(
            signature=sig,
            error=meta[sig]["error"],
            location=meta[sig]["location"],
            count=n,
            kinds=dict(kinds[sig]),
            sample=meta[sig]["sample"],
            first_seen=min(meta[sig]["ts"]).isoformat(sep=" ") if meta[sig]["ts"] else "",
            last_seen=max(meta[sig]["ts"]).isoformat(sep=" ") if meta[sig]["ts"] else "",
        )
        for sig, n in counts.most_common(TOP_N)
    ]
    return ErrorDigest(
        since=min(times).isoformat(sep=" ") if times else "",
        until=max(times).isoformat(sep=" ") if times else "",
        total_errors=int(sum(counts.values())),
        groups=groups,
    )


def render_error_digest(digest: ErrorDigest) -> str:
    """Render the digest as the compact text block handed to the analyst."""
    if not digest.groups:
        return "no errors in the window"
    lines = [f"window: {digest.since} .. {digest.until} ({digest.total_errors} error event(s))"]
    for g in digest.groups:
        kinds = ", ".join(f"{k}:{v}" for k, v in g.kinds.items())
        lines.append(f"- [{g.count}x] {g.signature}  ({kinds})")
        if g.sample:
            lines.append(f"    e.g. {g.sample}")
        lines.append(f"    first {g.first_seen} .. last {g.last_seen}")
    return "\n".join(lines)
