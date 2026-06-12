"""CronStore: CRUD, validation (exact-cron semantics), one-shot lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.cron import CronJob, CronStore, validate_schedule


def _store(tmp_path: Path) -> CronStore:
    return CronStore(tmp_path / "cron.json")


def test_add_list_get_remove_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path)

    job = store.add(CronJob(name="news", kind="cron", expr="0 9 * * *", message="AI brief"))

    assert store.get(job.id) is not None
    assert [j.id for j in store.list()] == [job.id]
    assert store.remove(job.id) is True
    assert store.list() == []
    assert store.remove(job.id) is False  # already gone


def test_bad_cron_expression_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid cron expression"):
        _store(tmp_path).add(CronJob(kind="cron", expr="61 * * *", message="x"))


def test_every_floor_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="minimum 30"):
        _store(tmp_path).add(CronJob(kind="every", expr="5", message="hot loop"))


def test_at_in_the_past_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="in the past"):
        _store(tmp_path).add(CronJob(kind="at", expr="2020-01-01T00:00:00", message="x"))


def test_at_jobs_are_one_shot(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = store.add(CronJob(kind="at", expr="2030-01-01T09:00:00", message="once"))

    assert job.delete_after_run is True  # forced for 'at'
    store.mark_ran(job.id)
    assert store.get(job.id) is None  # deleted after the run


def test_mark_ran_sets_last_run(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = store.add(CronJob(kind="every", expr="60", message="tick"))

    store.mark_ran(job.id)

    stored = store.get(job.id)
    assert stored is not None and stored.last_run is not None


def test_update_validates_and_replaces(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = store.add(CronJob(kind="cron", expr="0 9 * * *", message="x"))
    job.expr = "30 18 * * 5"

    store.update(job)

    assert store.get(job.id).expr == "30 18 * * 5"  # type: ignore[union-attr]
    job.expr = "nope"
    with pytest.raises(ValueError):
        store.update(job)


def test_validate_schedule_kinds() -> None:
    assert validate_schedule("cron", "*/5 * * * *") is None
    assert validate_schedule("every", "30") is None
    assert validate_schedule("weekly", "x") is not None  # unknown kind
