"""task_* tools: dict shapes, owner capture + per-user scoping, error paths (no raise)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from gaia.missions import TaskStore
from gaia.tools.task import (
    make_task_complete,
    make_task_create,
    make_task_get,
    make_task_list,
    make_task_plan,
    make_task_update,
)


def _ctx(user_id: str) -> Any:
    return SimpleNamespace(_invocation_context=SimpleNamespace(user_id=user_id))


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


def test_create_blocked_by_real_ids(tmp_path: Path) -> None:
    store = _store(tmp_path)
    create = make_task_create(store)
    a = create("a", tool_context=_ctx("itay"))["task"]
    b = create("b", tool_context=_ctx("itay"))["task"]
    out = create("x", blocked_by=f"{a['id']}, {b['id']}", tool_context=_ctx("itay"))
    assert out["task"]["blocked_by"] == [a["id"], b["id"]]


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


def test_create_rejects_unknown_blocked_by(tmp_path: Path) -> None:
    # The live bug: a made-up upstream id would silently block the task forever.
    out = make_task_create(_store(tmp_path))(
        "dependent", blocked_by="upstream task id not yet known", tool_context=_ctx("itay")
    )
    assert out["status"] == "error" and "unknown task id" in out["error_message"]


def test_plan_files_a_dag_with_real_edges(tmp_path: Path) -> None:
    store = _store(tmp_path)
    plan = json.dumps(
        [
            {"ref": "program", "title": "Design A/B program", "spec": "design it"},
            {"ref": "deploy", "title": "Deploy notes", "spec": "notes"},  # independent
            {"ref": "site", "title": "Build site", "spec": "build", "depends_on": ["program"]},
        ]
    )
    out = make_task_plan(store)(plan, tool_context=_ctx("itay"))

    assert out["status"] == "success"
    by_ref = {t["ref"]: t for t in out["tasks"]}
    # 'site' is wired to the REAL id of 'program'; independent tasks have no blockers.
    assert by_ref["site"]["blocked_by"] == [by_ref["program"]["id"]]
    assert by_ref["program"]["blocked_by"] == [] and by_ref["deploy"]["blocked_by"] == []
    # one shared mission; all owned by the caller
    assert len({t["mission_id"] for t in out["tasks"]}) == 1
    assert all(t["owner"] == "itay" for t in out["tasks"])
    # only the dependency feeds another → leaf detection
    assert store.has_dependents(by_ref["program"]["id"]) is True
    assert store.has_dependents(by_ref["site"]["id"]) is False


def test_plan_rejects_cycle(tmp_path: Path) -> None:
    plan = json.dumps(
        [
            {"ref": "a", "title": "A", "depends_on": ["b"]},
            {"ref": "b", "title": "B", "depends_on": ["a"]},
        ]
    )
    out = make_task_plan(_store(tmp_path))(plan, tool_context=_ctx("itay"))
    assert out["status"] == "error" and "cycle" in out["error_message"]


def test_plan_rejects_unknown_ref(tmp_path: Path) -> None:
    plan = json.dumps([{"ref": "a", "title": "A", "depends_on": ["ghost"]}])
    out = make_task_plan(_store(tmp_path))(plan, tool_context=_ctx("itay"))
    assert out["status"] == "error" and "unknown ref" in out["error_message"]


def test_plan_rejects_bad_json(tmp_path: Path) -> None:
    out = make_task_plan(_store(tmp_path))("not json", tool_context=_ctx("itay"))
    assert out["status"] == "error"


def test_create_captures_current_chat_as_notify_target(tmp_path: Path) -> None:
    from gaia.connectors.base import current_chat

    store = _store(tmp_path)
    token = current_chat.set(("whatsapp", "972@s.whatsapp.net"))
    try:
        out = make_task_create(store)("buy flights", tool_context=_ctx("itay"))
    finally:
        current_chat.reset(token)

    assert out["task"]["notify_channel"] == "whatsapp"
    assert out["task"]["notify_chat"] == "972@s.whatsapp.net"
