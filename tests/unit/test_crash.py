"""Crash capture: redacted report files + the recent/reported markers."""

from __future__ import annotations

import json

import pytest

from gaia import constants, crash


def test_write_crash_report_redacts_and_records(monkeypatch: pytest.MonkeyPatch) -> None:
    from gaia.config import Settings

    settings = Settings(telegram_bot_token="SECRET-TOKEN-123")
    try:
        raise ValueError("boom near SECRET-TOKEN-123")
    except ValueError as exc:
        path = crash.write_crash_report(exc, settings=settings, context={"connectors": ["cli"]})

    data = json.loads(path.read_text())
    assert path.parent == constants.CRASHES_DIR and path.suffix == ".json"
    assert data["error"].startswith("ValueError") and "SECRET-TOKEN-123" not in json.dumps(data)
    assert "ValueError" in data["traceback"] and data["connectors"] == ["cli"]
    assert data["gaia_version"] and data["python"]


def test_recent_crashes_filters_by_since() -> None:
    constants.CRASHES_DIR.mkdir(parents=True)
    (constants.CRASHES_DIR / "a.json").write_text("{}")
    assert len(crash.recent_crashes()) == 1
    # since just-now -> nothing newer
    import time

    assert crash.recent_crashes(since=time.time() + 1) == []


def test_reported_marker_roundtrip() -> None:
    assert crash.last_reported() == 0.0  # never reported
    crash.mark_reported()
    assert crash.last_reported() > 0.0
