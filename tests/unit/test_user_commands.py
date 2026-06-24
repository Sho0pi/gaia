"""Admin user-management commands: /users, /approve, /name, /link — and their gating."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from gaia.commands import default_registry
from gaia.commands.base import CommandContext
from gaia.users import UserStore


def _ctx(store: UserStore, *, args: str = "", role: str = "admin") -> CommandContext:
    gaia = SimpleNamespace(users=store, config=SimpleNamespace(), settings=SimpleNamespace())
    return CommandContext(
        args=args,
        gaia=gaia,  # type: ignore[arg-type]
        handler=SimpleNamespace(),  # type: ignore[arg-type]
        registry=default_registry(),
        user_id="itay",
        session_id="s",
        role=role,
    )


async def _run(name: str, ctx: CommandContext) -> Any:
    # Mirror the dispatch: ACL-authorize, then run (gating lives in authorize, not run()).
    from gaia.commands import authorize

    cmd = default_registry().get(name)
    return refusal if (refusal := authorize(cmd, ctx)) else await cmd.run(ctx)


def _store(tmp_path: Path) -> UserStore:
    return UserStore(tmp_path / "users.json")


async def test_non_admin_is_refused_and_store_untouched(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register("whatsapp", "972@s.whatsapp.net", "Grace", role="guest")

    out = await _run("approve", _ctx(store, args="grace user", role="user"))

    assert "admin" in out.lower()
    assert store.get("grace").role == "guest"  # type: ignore[union-attr] — unchanged


async def test_users_lists_everyone(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register("whatsapp", "111@s.whatsapp.net", "Itay", role="admin")
    store.register("whatsapp", "972@s.whatsapp.net", "Grace", role="guest")

    out = await _run("user", _ctx(store))

    assert "itay" in out and "grace" in out and "[admin]" in out and "[guest]" in out


async def test_approve_promotes_guest_by_qualified_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register("whatsapp", "972@s.whatsapp.net", "Grace", role="guest")

    out = await _run("approve", _ctx(store, args="whatsapp:972@s.whatsapp.net user"))

    assert "user" in out
    assert store.get("grace").role == "user"  # type: ignore[union-attr]


async def test_approve_rejects_bad_role(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register("whatsapp", "972@s.whatsapp.net", "Grace", role="guest")

    out = await _run("approve", _ctx(store, args="grace wizard"))

    assert "Usage" in out
    assert store.get("grace").role == "guest"  # type: ignore[union-attr]


async def test_name_sets_display_name(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register("whatsapp", "972@s.whatsapp.net", "", role="user")
    uid = store.list()[0].id

    await _run("name", _ctx(store, args=f"{uid} Grace"))

    assert store.get(uid).name == "Grace"  # type: ignore[union-attr]


async def test_link_attaches_channel_to_user(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register("whatsapp", "111@s.whatsapp.net", "Itay", role="admin")

    out = await _run("link", _ctx(store, args="itay telegram:42"))

    assert "itay" in out
    assert store.resolve("telegram", "42").id == "itay"  # type: ignore[union-attr]


async def test_approve_unknown_ref(tmp_path: Path) -> None:
    out = await _run("approve", _ctx(_store(tmp_path), args="ghost user"))

    assert "No user" in out


async def test_remove_deletes_user(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register("whatsapp", "972@s.whatsapp.net", "Grace", role="user")

    out = await _run("remove", _ctx(store, args="grace"))

    assert "Removed grace" in out
    assert store.get("grace") is None
    # the identity no longer resolves — a later message is a brand-new (gated) sender
    assert store.resolve("whatsapp", "972@s.whatsapp.net") is None


async def test_remove_self_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register("whatsapp", "111@s.whatsapp.net", "Itay", role="admin")

    out = await _run("remove", _ctx(store, args="itay"))  # caller is "itay"

    assert "yourself" in out.lower()
    assert store.get("itay") is not None


async def test_remove_requires_admin(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.register("whatsapp", "972@s.whatsapp.net", "Grace", role="user")

    out = await _run("remove", _ctx(store, args="grace", role="user"))

    assert "admin" in out.lower()
    assert store.get("grace") is not None  # untouched
