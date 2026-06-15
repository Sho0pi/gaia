"""P3 board guards + soul self-awareness: state-defaulted ids, depth/cycle/mission caps."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from gaia.missions import TaskStatus, TaskStore
from gaia.tools.task import (
    make_task_complete,
    make_task_create,
    make_task_get,
    make_task_plan,
    make_task_update,
)


def _ctx(user_id: str, state: dict[str, Any] | None = None) -> Any:
    """A fake ToolContext: a soul run carries session ``state`` (task_id/created_by)."""
    return SimpleNamespace(user_id=user_id, state=state or {})


def _store(tmp_path: Path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.db")


# -- soul self-awareness (state defaults) ---------------------------------------------


def test_soul_create_links_subtask_to_own_task_and_stamps_created_by(tmp_path: Path) -> None:
    store = _store(tmp_path)
    create = make_task_create(store)
    parent = create("root", tool_context=_ctx("itay"))["task"]

    # A soul running `parent` (state.task_id) files a subtask with no explicit parent_id.
    state = {"task_id": parent["id"], "created_by": "pt_coach"}
    sub = create("sub", tool_context=_ctx("itay", state))["task"]

    assert sub["parent_id"] == parent["id"]  # linked to the soul's own task
    assert sub["depth"] == 1 and sub["mission_id"] == parent["mission_id"]
    assert sub["created_by"] == "pt_coach"  # attributed to the soul, not gaia


def test_soul_update_and_get_default_to_own_task(tmp_path: Path) -> None:
    store = _store(tmp_path)
    own = make_task_create(store)("root", tool_context=_ctx("itay"))["task"]
    state = {"task_id": own["id"], "created_by": "pt_coach"}

    upd = make_task_update(store)(notes="working on it", tool_context=_ctx("itay", state))
    got = make_task_get(store)(tool_context=_ctx("itay", state))

    assert upd["status"] == "success" and upd["task"]["notes"] == "working on it"
    assert got["task"]["id"] == own["id"]


def test_gaia_turn_has_no_state_so_behaves_as_p1(tmp_path: Path) -> None:
    # No state → parent stays unset, created_by "gaia" (unchanged P1 behaviour).
    out = make_task_create(_store(tmp_path))("plain", tool_context=_ctx("itay"))
    assert out["task"]["parent_id"] == "" and out["task"]["created_by"] == "gaia"


# -- guards ---------------------------------------------------------------------------


def test_depth_cap_refuses_too_deep(tmp_path: Path) -> None:
    store = _store(tmp_path)
    create = make_task_create(store, max_depth=2)
    a = create("a", tool_context=_ctx("itay"))["task"]  # depth 0
    b = create("b", parent_id=a["id"], tool_context=_ctx("itay"))["task"]  # depth 1
    c = create("c", parent_id=b["id"], tool_context=_ctx("itay"))["task"]  # depth 2 (ok)
    out = create("d", parent_id=c["id"], tool_context=_ctx("itay"))  # depth 3 (refused)

    assert out["status"] == "error" and "depth" in out["error_message"]


def test_ancestry_cycle_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    create = make_task_create(store)
    a = create("a", tool_context=_ctx("itay"))["task"]
    b = create("b", parent_id=a["id"], tool_context=_ctx("itay"))["task"]
    # A child of b that waits on its ancestor a → deadlock cycle, refused.
    out = create("c", parent_id=b["id"], blocked_by=a["id"], tool_context=_ctx("itay"))

    assert out["status"] == "error" and "cycle" in out["error_message"]


def test_mission_task_cap_pauses_mission(tmp_path: Path) -> None:
    store = _store(tmp_path)
    create = make_task_create(store, max_tasks=2)
    root = create("root", tool_context=_ctx("itay"))["task"]
    create("t2", parent_id=root["id"], tool_context=_ctx("itay"))  # mission now has 2
    out = create("t3", parent_id=root["id"], tool_context=_ctx("itay"))  # breaches cap

    assert out["status"] == "error" and "limit" in out["error_message"]
    paused = store.get(root["mission_id"])
    assert paused is not None and paused.status == TaskStatus.AWAITING_APPROVAL


def test_task_plan_over_cap_refused(tmp_path: Path) -> None:
    plan = make_task_plan(_store(tmp_path), max_tasks=2)
    items = '[{"ref":"a","title":"A"},{"ref":"b","title":"B"},{"ref":"c","title":"C"}]'
    out = plan(items, tool_context=_ctx("itay"))
    assert out["status"] == "error" and "cap" in out["error_message"]


def test_complete_defaults_to_own_task(tmp_path: Path) -> None:
    store = _store(tmp_path)
    own = make_task_create(store)("root", tool_context=_ctx("itay"))["task"]
    state = {"task_id": own["id"]}
    out = make_task_complete(store)(result="done it", tool_context=_ctx("itay", state))

    assert out["status"] == "success" and out["task"]["status"] == "done"
    assert out["task"]["result"] == "done it"
