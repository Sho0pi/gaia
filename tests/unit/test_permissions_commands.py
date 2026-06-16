"""/grant, /revoke, /perms commands and UserStore grant/revoke."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from gaia.commands import default_registry
from gaia.commands.base import CommandContext
from gaia.users import UserStore


class _FakeDispatcher:
    def __init__(self) -> None:
        self.invalidated: list[str] = []

    async def invalidate_user(self, user_id: str) -> None:
        self.invalidated.append(user_id)


def _ctx(store: UserStore, *, args: str = "", role: str = "admin", uid: str = "root") -> Any:
    gaia = SimpleNamespace(
        users=store, config=None, settings=SimpleNamespace(), dispatcher=_FakeDispatcher()
    )
    return CommandContext(
        args=args,
        gaia=gaia,  # type: ignore[arg-type]
        handler=SimpleNamespace(),  # type: ignore[arg-type]
        registry=default_registry(),
        user_id=uid,
        session_id="s",
        role=role,
    )


async def _run(name: str, ctx: CommandContext) -> Any:
    return await default_registry().get(name).run(ctx)


def _store(tmp_path: Path) -> UserStore:
    store = UserStore(tmp_path / "users.json")
    store.register("cli", "root", "Root", role="admin")
    store.register("whatsapp", "111", "Alice", role="user")
    return store


async def test_grant_adds_capability(tmp_path: Path) -> None:
    store = _store(tmp_path)
    out = await _run("grant", _ctx(store, args="alice shell"))
    assert "shell" in out
    assert store.get("alice").grants == ["shell"]  # type: ignore[union-attr]


async def test_revoke_records_deny(tmp_path: Path) -> None:
    store = _store(tmp_path)
    out = await _run("revoke", _ctx(store, args="alice web"))
    assert "web" in out
    assert store.get("alice").denies == ["web"]  # type: ignore[union-attr]


async def test_grant_then_revoke_round_trips(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.grant("alice", "shell")
    store.revoke("alice", "shell")
    alice = store.get("alice")
    assert alice.grants == [] and alice.denies == ["shell"]  # type: ignore[union-attr]


async def test_non_admin_grant_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    out = await _run("grant", _ctx(store, args="alice shell", role="user", uid="alice"))
    assert "admin" in out.lower()
    assert store.get("alice").grants == []  # type: ignore[union-attr]


async def test_perms_self_service(tmp_path: Path) -> None:
    store = _store(tmp_path)
    out = await _run("perms", _ctx(store, role="user", uid="alice"))
    assert "alice" in out and "effective" in out


async def test_grant_invalidates_target_handler(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ctx = _ctx(store, args="alice shell")
    await _run("grant", ctx)
    assert ctx.gaia.dispatcher.invalidated == ["alice"]  # type: ignore[attr-defined]


async def test_acl_lists_groups_and_defaults(tmp_path: Path) -> None:
    out = await _run("acl", _ctx(_store(tmp_path)))
    assert "shell" in out and "exec" in out  # a group and one of its tools
    assert "Role defaults" in out and "admin" in out


async def test_old_users_json_without_acl_loads(tmp_path: Path) -> None:
    # Backward compat: a users.json predating grants/denies still loads with empty lists.
    path = tmp_path / "users.json"
    path.write_text('[{"id": "x", "role": "user", "identities": []}]\n')
    user = UserStore(path).get("x")
    assert user is not None and user.grants == [] and user.denies == []
