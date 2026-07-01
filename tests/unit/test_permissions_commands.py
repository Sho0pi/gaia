"""/grant, /revoke, /perms commands and UserStore grant/revoke."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from gaia.commands import default_registry
from gaia.commands.base import CommandContext
from gaia.users import UserStore


def _ctx(store: UserStore, *, args: str = "", role: str = "admin", uid: str = "root") -> Any:
    gaia = SimpleNamespace(users=store, config=None, settings=SimpleNamespace())
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
    # Mirror the dispatch: ACL-authorize, then run (gating lives in authorize, not run()).
    from gaia.commands import authorize

    cmd = default_registry().get(name)
    return refusal if (refusal := authorize(cmd, ctx)) else await cmd.run(ctx)


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


async def test_grant_rejects_unknown_capability(tmp_path: Path) -> None:
    # The 'reminder for cron' bug: a wrong name is refused with the valid list, not stored silently.
    store = _store(tmp_path)
    out = await _run("grant", _ctx(store, args="alice reminder"))
    assert "Unknown capability" in out and "reminder" in out and "cron" in out
    assert store.get("alice").grants == []  # type: ignore[union-attr]


async def test_grant_accepts_group_toolid_and_wildcard(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert "cron" in await _run("grant", _ctx(store, args="alice cron"))  # a group name
    assert "web_fetch" in await _run("grant", _ctx(store, args="alice web_fetch"))  # a tool id
    assert "*" in await _run("grant", _ctx(store, args="alice *"))  # the wildcard
    assert store.get("alice").grants == ["cron", "web_fetch", "*"]  # type: ignore[union-attr]


async def test_revoke_rejects_unknown_capability(tmp_path: Path) -> None:
    store = _store(tmp_path)
    out = await _run("revoke", _ctx(store, args="alice reminder"))
    assert "Unknown capability" in out
    assert store.get("alice").denies == []  # type: ignore[union-attr]


def test_capability_error_helper() -> None:
    from gaia.acl import capability_error, known_capabilities

    assert capability_error("reminder") is not None  # not a capability
    assert capability_error("cron") is None  # a group
    assert capability_error("web_fetch") is None  # a tool id
    assert capability_error("*") is None  # the wildcard
    assert {"cron", "web", "web_fetch", "*"} <= known_capabilities()


async def test_non_admin_grant_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    out = await _run("grant", _ctx(store, args="alice shell", role="user", uid="alice"))
    assert "admin" in out.lower()
    assert store.get("alice").grants == []  # type: ignore[union-attr]


async def test_perms_self_service(tmp_path: Path) -> None:
    store = _store(tmp_path)
    out = await _run("perms", _ctx(store, role="user", uid="alice"))
    assert "alice" in out and "effective" in out


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
