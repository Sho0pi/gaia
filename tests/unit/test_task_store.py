"""TaskStore: CRUD, filtered list, ready-task query, parent linkage, JSON cols, WAL."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from gaia.missions import Task, TaskStatus, TaskStore

# A P1 schema snapshot (no notify_channel/notify_chat) to test the migration.
_P1_SCHEMA = (
    "CREATE TABLE tasks (id TEXT PRIMARY KEY, mission_id TEXT DEFAULT '', parent_id TEXT "
    "DEFAULT '', title TEXT DEFAULT '', spec TEXT DEFAULT '', status TEXT DEFAULT 'inbox', "
    "assignee TEXT DEFAULT '', blocked_by TEXT DEFAULT '[]', depth INTEGER DEFAULT 0, "
    "artifacts TEXT DEFAULT '[]', result TEXT DEFAULT '', notes TEXT DEFAULT '', owner TEXT "
    "DEFAULT '', created_by TEXT DEFAULT '', approval_class TEXT DEFAULT '', budget_used REAL "
    "DEFAULT 0, created_at TEXT, updated_at TEXT)"
)


def _store(tmp_path: Path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.db")


def test_migration_adds_notify_columns_to_old_db(tmp_path: Path) -> None:
    db = tmp_path / "tasks.db"
    conn = sqlite3.connect(db)
    conn.executescript(_P1_SCHEMA)
    conn.execute(
        "INSERT INTO tasks (id, title, created_at, updated_at) VALUES (?,?,?,?)",
        ("old1", "legacy", "t", "t"),
    )
    conn.commit()
    conn.close()

    store = TaskStore(db)  # opening runs the idempotent migration
    legacy = store.get("old1")
    assert legacy is not None and legacy.title == "legacy"  # row survived
    assert legacy.notify_channel == "" and legacy.notify_chat == ""  # new cols default empty
    # writes using the new columns work, and re-opening is a no-op
    store.create(Task(title="new", notify_channel="whatsapp", notify_chat="972@x"))
    assert TaskStore(db).get("old1") is not None  # second open doesn't break


def test_create_get_list_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    t = store.create(Task(title="research", owner="itay", created_by="gaia"))

    assert t.mission_id == t.id  # a root is its own mission
    got = store.get(t.id)
    assert got is not None and got.title == "research" and got.owner == "itay"
    assert [x.id for x in store.list()] == [t.id]


def test_list_filters_by_mission_status_owner(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a = store.create(Task(title="a", owner="itay"))
    store.create(Task(title="b", owner="grace"))
    store.update_status(a.id, TaskStatus.RUNNING)

    assert {t.title for t in store.list(owner="itay")} == {"a"}
    assert {t.title for t in store.list(owner="grace")} == {"b"}
    assert {t.title for t in store.list(status=TaskStatus.RUNNING)} == {"a"}
    assert {t.title for t in store.list(mission=a.mission_id)} == {"a"}


def test_parent_linkage_sets_depth_and_mission(tmp_path: Path) -> None:
    store = _store(tmp_path)
    root = store.create(Task(title="root", owner="itay"))
    child = store.create(
        Task(title="child", owner="itay", parent_id=root.id, mission_id=root.mission_id, depth=1)
    )

    assert child.parent_id == root.id and child.depth == 1
    assert child.mission_id == root.mission_id


def test_post_result_marks_done_and_roundtrips_json_cols(tmp_path: Path) -> None:
    store = _store(tmp_path)
    t = store.create(Task(title="x", owner="itay", blocked_by=["dep1", "dep2"]))

    done = store.post_result(t.id, "shipped", artifacts=["/w/a.html", "/w/b.css"])
    assert done is not None and done.status is TaskStatus.DONE and done.result == "shipped"
    again = store.get(t.id)
    assert again is not None
    assert again.artifacts == ["/w/a.html", "/w/b.css"]
    assert again.blocked_by == ["dep1", "dep2"]  # list cols survive the sqlite roundtrip


def test_update_bumps_updated_at(tmp_path: Path) -> None:
    store = _store(tmp_path)
    t = store.create(Task(title="x", owner="itay"))
    t.updated_at = "2000-01-01T00:00:00"  # force an old stamp

    refreshed = store.update(t)
    assert refreshed.updated_at != "2000-01-01T00:00:00"


def test_children_lists_subtasks_and_open_only_filter(tmp_path: Path) -> None:
    store = _store(tmp_path)
    parent = store.create(Task(title="parent", owner="itay"))
    a = store.create(Task(title="a", owner="itay", parent_id=parent.id))
    b = store.create(Task(title="b", owner="itay", parent_id=parent.id))
    store.create(Task(title="unrelated", owner="itay"))  # not a child

    assert {t.id for t in store.children(parent.id)} == {a.id, b.id}

    store.post_result(a.id, "done")  # a → done
    open_kids = store.children(parent.id, open_only=True)
    assert {t.id for t in open_kids} == {b.id}  # the finished child is dropped


def test_ready_tasks_excludes_blocked_until_deps_done(tmp_path: Path) -> None:
    store = _store(tmp_path)
    dep = store.create(Task(title="dep", owner="itay"))
    blocked = store.create(Task(title="blocked", owner="itay", blocked_by=[dep.id]))
    free = store.create(Task(title="free", owner="itay"))

    ready = {t.id for t in store.ready_tasks()}
    assert ready == {dep.id, free.id}  # blocked one is held back

    store.post_result(dep.id, "ok")
    ready = {t.id for t in store.ready_tasks()}
    assert blocked.id in ready  # now unblocked
    assert dep.id not in ready  # done, no longer in the inbox


def test_concurrent_read_during_write_is_wal_safe(tmp_path: Path) -> None:
    # A writer thread inserting while the main thread reads — WAL must not raise
    # 'database is locked'. (Acceptance criterion of #127.)
    store = _store(tmp_path)
    errors: list[Exception] = []

    def writer() -> None:
        try:
            for i in range(60):
                store.create(Task(title=f"t{i}", owner="itay"))
        except Exception as exc:  # pragma: no cover - the thing we assert never happens
            errors.append(exc)

    th = threading.Thread(target=writer)
    th.start()
    try:
        for _ in range(60):
            store.list(owner="itay")
    finally:
        th.join()

    assert errors == []
    assert len(store.list()) == 60
