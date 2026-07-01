"""System test: the missions task board on real SQLite - persistence + dependency readiness.

Marked ``system`` because it exercises the actual sqlite3 store (WAL file on disk), not a fake.
No external service, no key - runs in milliseconds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.missions.store import Task, TaskStatus, TaskStore

pytestmark = pytest.mark.system


def _store(tmp_path: Path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.db")


def test_create_get_list_roundtrip(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.create(Task(id="a", title="root", owner="itay", status=TaskStatus.INBOX))
    s.create(Task(id="b", title="other", owner="mei", status=TaskStatus.INBOX))

    assert s.get("a") is not None and s.get("a").title == "root"  # type: ignore[union-attr]
    assert [t.id for t in s.list(owner="itay")] == ["a"]  # owner filter
    assert s.get("missing") is None


def test_ready_tasks_respects_blocked_by(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.create(Task(id="dep", title="first", status=TaskStatus.INBOX))
    s.create(Task(id="child", title="after", status=TaskStatus.INBOX, blocked_by=["dep"]))

    ready = {t.id for t in s.ready_tasks()}
    assert "dep" in ready and "child" not in ready  # child waits on its blocker

    s.update_status("dep", TaskStatus.DONE)
    ready = {t.id for t in s.ready_tasks()}
    assert "child" in ready  # blocker done → child becomes ready


def test_status_filter_and_update(tmp_path: Path) -> None:
    s = _store(tmp_path)
    s.create(Task(id="t", title="x", status=TaskStatus.INBOX))
    s.update_status("t", TaskStatus.RUNNING)

    assert s.get("t").status is TaskStatus.RUNNING  # type: ignore[union-attr]
    assert [t.id for t in s.list(status=TaskStatus.RUNNING)] == ["t"]
    assert s.list(status=TaskStatus.DONE) == []


def test_persists_across_store_instances(tmp_path: Path) -> None:
    _store(tmp_path).create(Task(id="p", title="persisted"))
    # a fresh store on the same db file sees the row (real WAL persistence)
    reopened = TaskStore(tmp_path / "tasks.db").get("p")
    assert reopened is not None and reopened.title == "persisted"
