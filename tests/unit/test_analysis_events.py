"""Event reader + digest: jsonl parsing, rotation, windowing, aggregation, rendering."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from gaia.analysis import digest_events, read_events, render_digest

NOW = datetime(2026, 6, 12, 12, 0, 0)


def _line(ts: datetime, message: str, **fields: object) -> str:
    return json.dumps(
        {"asctime": ts.strftime("%Y-%m-%d %H:%M:%S,000"), "message": message, **fields}
    )


def _write(path: Path, *lines: str) -> None:
    path.write_text("\n".join(lines) + "\n")


def test_read_events_parses_and_windows(tmp_path: Path) -> None:
    _write(
        tmp_path / "events.jsonl",
        _line(NOW - timedelta(days=10), "message_in", user="old"),
        _line(NOW - timedelta(hours=1), "message_in", user="recent"),
        "not json at all {{{",
        _line(NOW - timedelta(minutes=5), "tool_used", tool="web_search", status="success"),
    )

    events = read_events(tmp_path, NOW - timedelta(days=7))

    assert [e["message"] for e in events] == ["message_in", "tool_used"]
    assert events[0]["user"] == "recent"  # the 10-day-old line was windowed out


def test_read_events_includes_rotated_backups_oldest_first(tmp_path: Path) -> None:
    _write(tmp_path / "events.jsonl.2", _line(NOW - timedelta(hours=3), "message_in", user="a"))
    _write(tmp_path / "events.jsonl.1", _line(NOW - timedelta(hours=2), "message_in", user="b"))
    _write(tmp_path / "events.jsonl", _line(NOW - timedelta(hours=1), "message_in", user="c"))

    events = read_events(tmp_path, NOW - timedelta(days=1))

    assert [e["user"] for e in events] == ["a", "b", "c"]


def test_read_events_missing_dir_is_empty(tmp_path: Path) -> None:
    assert read_events(tmp_path / "nope", NOW) == []


def test_digest_aggregates_counts_and_sequences(tmp_path: Path) -> None:
    lines = []
    for i in range(3):  # the same search->fetch sequence three times, one session
        base = NOW - timedelta(minutes=30 - i * 5)
        lines += [
            _line(base, "message_in", user="itay", session="s1"),
            _line(base, "tool_used", tool="web_search", status="success", session="s1"),
            _line(base, "tool_used", tool="web_fetch", status="success", session="s1"),
            _line(base, "message_out", user="itay"),
        ]
    lines.append(_line(NOW, "turn_error", user="itay", error="TimeoutError"))
    lines.append(_line(NOW, "command_used", command="help", status="ok"))
    _write(tmp_path / "events.jsonl", *lines)

    digest = digest_events(read_events(tmp_path, NOW - timedelta(days=1)))

    assert digest.users == {"itay": 3}
    assert digest.messages_out == 3
    assert digest.tools == {"web_search (success)": 3, "web_fetch (success)": 3}
    assert digest.tool_sequences["web_search -> web_fetch"] == 3
    assert digest.errors == {"TimeoutError": 1}
    assert digest.commands == {"help": 1}
    assert digest.total_events == len(lines)


def test_render_digest_is_compact_and_carries_numbers(tmp_path: Path) -> None:
    _write(
        tmp_path / "events.jsonl",
        _line(NOW, "message_in", user="itay", session="s1"),
        _line(NOW, "tool_used", tool="web_search", status="success", session="s1"),
    )
    digest = digest_events(read_events(tmp_path, NOW - timedelta(days=1)))

    text = render_digest(digest)

    assert "itay: 1" in text
    assert "web_search (success): 1" in text
    assert len(text.splitlines()) < 40  # compact: a digest, not a dump
