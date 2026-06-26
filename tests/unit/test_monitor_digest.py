"""Error digest grouping (monitor/digest) + the error_details helper (logs)."""

from __future__ import annotations

from datetime import datetime

from gaia.logs import error_details
from gaia.monitor.digest import error_digest, render_error_digest


def _ev(message: str, ts: str, **fields: object) -> dict[str, object]:
    return {"message": message, "_ts": datetime.fromisoformat(ts), **fields}


def test_groups_three_event_kinds_by_signature() -> None:
    events = [
        _ev(
            "turn_error",
            "2026-06-20 10:00:00",
            error="ValueError",
            where="handler.py:42",
            detail="bad x",
        ),
        _ev(
            "turn_error",
            "2026-06-20 11:00:00",
            error="ValueError",
            where="handler.py:42",
            detail="bad y",
        ),
        _ev(
            "tool_used",
            "2026-06-20 12:00:00",
            status="error",
            error="TimeoutError",
            tool="web_fetch",
            detail="timed out",
        ),
        _ev(
            "error",
            "2026-06-20 13:00:00",
            source="cron_runner",
            error="KeyError",
            where="loop.py:9",
            detail="missing",
        ),
        _ev(
            "tool_used", "2026-06-20 14:00:00", status="ok", tool="fs_write"
        ),  # not an error -> ignored
        _ev("message_in", "2026-06-20 15:00:00", user="u1"),  # not an error -> ignored
    ]
    digest = error_digest(events)

    assert digest.total_errors == 4
    sigs = {g.signature: g for g in digest.groups}
    assert sigs["ValueError @ handler.py:42"].count == 2  # grouped
    assert sigs["ValueError @ handler.py:42"].sample == "bad x"  # first detail kept
    assert sigs["TimeoutError @ web_fetch"].count == 1
    assert sigs["KeyError @ loop.py:9"].kinds == {"error": 1}
    # top group first
    assert digest.groups[0].signature == "ValueError @ handler.py:42"


def test_excludes_the_monitors_own_errors() -> None:
    # The monitor must not report on itself (a meta-loop): error events with source=monitor_* drop.
    events = [
        _ev(
            "error",
            "2026-06-20 10:00:00",
            source="monitor_loop",
            error="ValidationError",
            where="loop.py:91",
        ),
        _ev("error", "2026-06-20 11:00:00", source="monitor_scheduler", error="X", where="s.py:1"),
        _ev("error", "2026-06-20 12:00:00", source="cron_runner", error="KeyError", where="c.py:9"),
    ]
    digest = error_digest(events)
    assert digest.total_errors == 1  # only the non-monitor one
    assert digest.groups[0].signature == "KeyError @ c.py:9"


def test_render_empty_and_nonempty() -> None:
    assert render_error_digest(error_digest([])) == "no errors in the window"
    text = render_error_digest(
        error_digest([_ev("turn_error", "2026-06-20 10:00:00", error="ValueError", where="h.py:1")])
    )
    assert "ValueError @ h.py:1" in text and "1x" in text


def test_error_details_extracts_message_and_gaia_frame() -> None:
    try:
        raise ValueError("boom" * 200)  # long -> truncated
    except ValueError as exc:
        detail, where = error_details(exc)
    assert detail.startswith("boom") and len(detail) <= 300
    # this test file is not under site-packages, so the frame is captured
    assert where.startswith("test_monitor_digest.py:")
