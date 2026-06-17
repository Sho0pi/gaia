"""run_command tool: agent_access tiers + dispatch to a command as the calling user."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from gaia.tools.command import make_run_command
from gaia.users import UserStore


def _gaia(store: UserStore) -> Any:
    return SimpleNamespace(users=store, config=None)


def _ctx(user_id: str) -> Any:
    return SimpleNamespace(user_id=user_id, session_id="s")


async def _run(store: UserStore, command: str, args: str, uid: str) -> dict[str, Any]:
    tool = make_run_command(_gaia(store), handler=None)
    return await tool(command, args, tool_context=_ctx(uid))


def _store(tmp_path: Path) -> UserStore:
    store = UserStore(tmp_path / "users.json")
    store.register("cli", "root", "Root", role="admin")
    store.register("whatsapp", "111", "Bob", role="user")
    return store


async def test_unknown_command(tmp_path: Path) -> None:
    out = await _run(_store(tmp_path), "nope", "", "root")
    assert out["status"] == "error" and "unknown" in out["error_message"]


async def test_user_tier_runs(tmp_path: Path) -> None:
    # help is agent_access="user" — any caller's agent may run it (needs only ctx.registry).
    out = await _run(_store(tmp_path), "help", "", "bob")
    assert out["status"] == "success" and "reply" in out


async def test_admin_tier_refused_for_non_admin(tmp_path: Path) -> None:
    # grant is agent_access="admin"; Bob (user) -> refused before the command even runs.
    out = await _run(_store(tmp_path), "grant", "bob shell", "bob")
    assert out["status"] == "error" and "admin" in out["error_message"]


async def test_admin_tier_allowed_for_admin(tmp_path: Path) -> None:
    store = _store(tmp_path)
    out = await _run(store, "grant", "bob shell", "root")
    assert out["status"] == "success"
    assert "shell" in store.get("bob").grants  # type: ignore[union-attr]


async def test_handler_dependent_refused_without_handler(tmp_path: Path) -> None:
    # reset needs the live handler; with handler=None it's refused, not crashed.
    out = await _run(_store(tmp_path), "reset", "", "root")
    assert out["status"] == "error"
