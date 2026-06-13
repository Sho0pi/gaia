"""task_* tools: dict shapes, owner capture + per-user scoping, error paths (no raise)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from gaia.missions import TaskStore
from gaia.tools.task import (
    make_task_complete,
    make_task_create,
    make_task_get,
    make_task_list,
    make_task_update,
)


def _ctx(user_id: str) -> Any:
    return SimpleNamespace(user_id=user_id)  # ADK public ToolContext.user_id


def _store(tmp_path: Path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.db")


def test_create_captures_owner_and_returns_task(tmp_path: Path) -> None:
    store = _store(tmp_path)
    out = make_task_create(store)("buy flights", spec="TLV->NYC", tool_context=_ctx("itay"))

    assert out["status"] == "success"
    assert out["task"]["owner"] == "itay" and out["task"]["created_by"] == "gaia"
    assert out["task"]["status"] == "inbox"


def test_create_empty_title_errors(tmp_path: Path) -> None:
    out = make_task_create(_store(tmp_path))("  ", tool_context=_ctx("itay"))
    assert out["status"] == "error" and "title" in out["error_message"]


def test_create_with_parent_sets_depth(tmp_path: Path) -> None:
    store = _store(tmp_path)
    create = make_task_create(store)
    root = create("root", tool_context=_ctx("itay"))["task"]
    child = create("child", parent_id=root["id"], tool_context=_ctx("itay"))["task"]

    assert child["depth"] == 1 and child["parent_id"] == root["id"]
    assert child["mission_id"] == root["mission_id"]


def test_create_blocked_by_csv_split(tmp_path: Path) -> None:
    store = _store(tmp_path)
    out = make_task_create(store)("x", blocked_by="a, b ,c", tool_context=_ctx("itay"))
    assert out["task"]["blocked_by"] == ["a", "b", "c"]


def test_list_is_owner_scoped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    make_task_create(store)("mine", tool_context=_ctx("itay"))
    make_task_create(store)("hers", tool_context=_ctx("grace"))

    itay = make_task_list(store)(tool_context=_ctx("itay"))
    assert [t["title"] for t in itay["tasks"]] == ["mine"]


def test_get_and_update_reject_other_owners_task(tmp_path: Path) -> None:
    store = _store(tmp_path)
    hers = make_task_create(store)("hers", tool_context=_ctx("grace"))["task"]

    got = make_task_get(store)(hers["id"], tool_context=_ctx("itay"))
    assert got["status"] == "error" and "no task" in got["error_message"]

    upd = make_task_update(store)(hers["id"], status="running", tool_context=_ctx("itay"))
    assert upd["status"] == "error"


def test_update_validates_status(tmp_path: Path) -> None:
    store = _store(tmp_path)
    t = make_task_create(store)("x", tool_context=_ctx("itay"))["task"]
    out = make_task_update(store)(t["id"], status="bogus", tool_context=_ctx("itay"))
    assert out["status"] == "error" and "unknown status" in out["error_message"]


def test_complete_sets_done_with_result_and_artifacts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    t = make_task_create(store)("x", tool_context=_ctx("itay"))["task"]

    out = make_task_complete(store)(
        t["id"], result="done it", artifacts="/w/a.md,/w/b.md", tool_context=_ctx("itay")
    )
    assert out["status"] == "success"
    assert out["task"]["status"] == "done" and out["task"]["result"] == "done it"
    assert out["task"]["artifacts"] == ["/w/a.md", "/w/b.md"]


def test_get_unknown_id_errors(tmp_path: Path) -> None:
    out = make_task_get(_store(tmp_path))("nope", tool_context=_ctx("itay"))
    assert out["status"] == "error"
