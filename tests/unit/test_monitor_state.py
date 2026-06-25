"""Monitor dedup state: filter_new suppresses signatures reported within the cooldown."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gaia.monitor import state


def test_first_time_is_fresh_then_suppressed() -> None:
    assert state.filter_new(["a", "b"], cooldown_hours=24) == ["a", "b"]  # first sighting
    assert state.filter_new(["a", "b"], cooldown_hours=24) == []  # within cooldown -> suppressed
    assert state.filter_new(["a", "c"], cooldown_hours=24) == ["c"]  # only the new one


def test_fresh_again_after_cooldown(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    state.filter_new(["a"], cooldown_hours=24)
    # rewrite the record to look old, then it should report again
    path = state._path()
    import json

    path.write_text(json.dumps({"a": (datetime.now(UTC) - timedelta(hours=48)).isoformat()}))
    assert state.filter_new(["a"], cooldown_hours=24) == ["a"]
