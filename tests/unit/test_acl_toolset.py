"""AclToolset resolves the caller's allowed tools live on each get_tools call."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from gaia.core.acl_toolset import AclToolset
from gaia.users import UserStore


def _func(name: str) -> Any:
    async def f() -> dict[str, Any]:
        return {}

    f.__name__ = name
    return f


class _Registry:
    def __init__(self) -> None:
        self._t = {n: _func(n) for n in ("web_fetch", "exec", "remember")}

    def names(self) -> list[str]:
        return sorted(self._t)

    def get(self, name: str) -> Any:
        return self._t[name]


def _gaia(store: UserStore) -> Any:
    return SimpleNamespace(tools=_Registry(), users=store, config=None)


def _ctx(user_id: str | None) -> Any:
    return SimpleNamespace(user_id=user_id, invocation_id="inv1")


async def _names(toolset: AclToolset, user_id: str | None) -> set[str]:
    return {t.name for t in await toolset.get_tools(_ctx(user_id))}


async def test_user_sees_role_tools_not_shell(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "users.json")
    store.register("cli", "bob", "Bob", role="user")
    names = await _names(AclToolset(_gaia(store)), "bob")  # type: ignore[arg-type]
    assert "web_fetch" in names and "remember" in names
    assert "exec" not in names


async def test_grant_takes_effect_on_next_get_tools(tmp_path: Path) -> None:
    # The whole point: no rebuild — a later get_tools reflects a grant made in between.
    store = UserStore(tmp_path / "users.json")
    store.register("cli", "alice", "Alice", role="user")
    toolset = AclToolset(_gaia(store))  # type: ignore[arg-type]

    assert "exec" not in await _names(toolset, "alice")
    store.grant("alice", "shell")
    assert "exec" in await _names(toolset, "alice")


async def test_none_user_gets_everything(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "users.json")
    names = await _names(AclToolset(_gaia(store)), None)  # type: ignore[arg-type]
    assert {"web_fetch", "exec", "remember"} <= names
