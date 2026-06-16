"""ToolPermissionPlugin: the hard ACL gate — deny tools the caller's role can't use."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from gaia.core.plugins import ToolPermissionPlugin
from gaia.users import UserStore

_REGISTRY_IDS = ["web_fetch", "exec", "remember", "task_create"]


def _gaia(store: UserStore) -> Any:
    tools = SimpleNamespace(names=lambda: list(_REGISTRY_IDS))
    return SimpleNamespace(users=store, config=None, tools=tools)


def _tool(name: str) -> Any:
    return SimpleNamespace(name=name)


def _ctx(user_id: str | None) -> Any:
    return SimpleNamespace(user_id=user_id)


async def _call(plugin: ToolPermissionPlugin, tool: str, user_id: str | None) -> Any:
    return await plugin.before_tool_callback(
        tool=_tool(tool), tool_args={}, tool_context=_ctx(user_id)
    )


async def test_user_allowed_tool_passes(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "users.json")
    store.register("cli", "bob", "Bob", role="user")
    plugin = ToolPermissionPlugin(_gaia(store))  # type: ignore[arg-type]
    assert await _call(plugin, "web_fetch", "bob") is None


async def test_user_denied_tool_short_circuits(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "users.json")
    store.register("cli", "bob", "Bob", role="user")
    plugin = ToolPermissionPlugin(_gaia(store))  # type: ignore[arg-type]
    out = await _call(plugin, "exec", "bob")
    assert out is not None and out["status"] == "error" and "exec" in out["error_message"]


async def test_grant_unlocks_tool(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "users.json")
    store.register("cli", "alice", "Alice", role="user")
    store.grant("alice", "shell")
    plugin = ToolPermissionPlugin(_gaia(store))  # type: ignore[arg-type]
    assert await _call(plugin, "exec", "alice") is None


async def test_admin_passes_everything(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "users.json")
    store.register("cli", "root", "Root", role="admin")
    plugin = ToolPermissionPlugin(_gaia(store))  # type: ignore[arg-type]
    assert await _call(plugin, "exec", "root") is None


async def test_non_registry_tool_not_gated(tmp_path: Path) -> None:
    # delegate_to_soul / MCP tools aren't in the registry — never ACL'd here.
    store = UserStore(tmp_path / "users.json")
    store.register("cli", "bob", "Bob", role="user")
    plugin = ToolPermissionPlugin(_gaia(store))  # type: ignore[arg-type]
    assert await _call(plugin, "delegate_to_soul", "bob") is None


async def test_mcp_browser_tool_is_gated_by_prefix_off_registry(tmp_path: Path) -> None:
    # playwright-mcp browser_* tools attach as an MCP toolset (not the registry); the
    # browser_* PREFIX rule governs them, so a user without 'browser' is denied — even a
    # tool name the GROUPS set never enumerates (browser_tab_new).
    store = UserStore(tmp_path / "users.json")
    store.register("cli", "bob", "Bob", role="user")
    store.revoke("bob", "browser")  # user role has browser by default; take it away
    plugin = ToolPermissionPlugin(_gaia(store))  # type: ignore[arg-type]
    for tool in ("browser_navigate", "browser_tab_new"):  # neither in _REGISTRY_IDS
        out = await _call(plugin, tool, "bob")
        assert out is not None and out["status"] == "error"


async def test_browser_cap_allows_mcp_browser_tool(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "users.json")
    store.register("cli", "bob", "Bob", role="user")  # user role includes browser
    plugin = ToolPermissionPlugin(_gaia(store))  # type: ignore[arg-type]
    assert await _call(plugin, "browser_tab_new", "bob") is None


async def test_unresolved_user_is_trusted(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "users.json")
    plugin = ToolPermissionPlugin(_gaia(store))  # type: ignore[arg-type]
    assert await _call(plugin, "exec", "gaia-user") is None
    assert await _call(plugin, "exec", None) is None
