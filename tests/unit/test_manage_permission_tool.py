"""The manage_permission tool: admin grants/revokes ACL capabilities from chat."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from gaia.tools.permission import make_manage_permission
from gaia.users import UserStore


def _gaia(store: UserStore) -> Any:
    return SimpleNamespace(users=store, config=None)


def _store(tmp_path: Path) -> UserStore:
    store = UserStore(tmp_path / "users.json")
    store.register("cli", "root", "Root", role="admin")
    store.register("whatsapp", "111", "Alice", role="user")
    return store


def _ctx(uid: str) -> Any:
    return SimpleNamespace(user_id=uid)


async def test_admin_can_grant(tmp_path: Path) -> None:
    store = _store(tmp_path)
    gaia = _gaia(store)
    tool = make_manage_permission(gaia)
    out = await tool(user="alice", action="grant", capability="shell", tool_context=_ctx("root"))
    assert out["status"] == "success"
    assert store.get("alice").grants == ["shell"]  # type: ignore[union-attr]


async def test_non_admin_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tool = make_manage_permission(_gaia(store))
    out = await tool(user="alice", action="grant", capability="shell", tool_context=_ctx("alice"))
    assert out["status"] == "error" and "admin" in out["error_message"]
    assert store.get("alice").grants == []  # type: ignore[union-attr]


async def test_bad_action(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tool = make_manage_permission(_gaia(store))
    out = await tool(user="alice", action="nuke", capability="shell", tool_context=_ctx("root"))
    assert out["status"] == "error"


async def test_unknown_user(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tool = make_manage_permission(_gaia(store))
    out = await tool(user="nobody", action="grant", capability="web", tool_context=_ctx("root"))
    assert out["status"] == "error"


async def test_revoke_records_deny(tmp_path: Path) -> None:
    store = _store(tmp_path)
    tool = make_manage_permission(_gaia(store))
    out = await tool(user="alice", action="revoke", capability="web", tool_context=_ctx("root"))
    assert out["status"] == "success"
    assert store.get("alice").denies == ["web"]  # type: ignore[union-attr]
