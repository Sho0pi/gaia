"""ACL integration against the REAL tool registry (no model key needed).

Unit tests use a hand-picked id slice; this proves the capability groups line up with the
*actual* registered tool ids — every registered tool belongs to a group (so nothing is
silently ungrantable), and role/grant resolution picks the right real tools.
"""

from __future__ import annotations

from pathlib import Path

from gaia.acl import allowed_tool_ids
from gaia.acl.groups import GROUPS
from gaia.core.plugins import ToolPermissionPlugin
from gaia.tools.registry import default_registry
from gaia.users import UserStore


def _ids() -> set[str]:
    return set(default_registry().names())


def test_every_registered_tool_belongs_to_a_group() -> None:
    grouped = set().union(*GROUPS.values())
    orphans = _ids() - grouped
    assert orphans == set(), f"registry tools in no ACL group (ungrantable): {orphans}"


def test_user_role_excludes_shell_against_real_registry() -> None:
    from gaia.users.store import User

    ids = _ids()
    allowed = allowed_tool_ids(User(id="u", role="user"), None, ids)
    assert "exec" not in allowed
    if "web_fetch" in ids:
        assert "web_fetch" in allowed


async def test_plugin_denies_then_grant_unlocks_real_exec(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "users.json")
    store.register("cli", "alice", "Alice", role="user")
    from types import SimpleNamespace

    gaia = SimpleNamespace(
        users=store, config=None, tools=SimpleNamespace(names=lambda: list(_ids()))
    )
    plugin = ToolPermissionPlugin(gaia)  # type: ignore[arg-type]
    tool = SimpleNamespace(name="exec")
    ctx = SimpleNamespace(user_id="alice")

    denied = await plugin.before_tool_callback(tool=tool, tool_args={}, tool_context=ctx)
    assert denied is not None and denied["status"] == "error"

    store.grant("alice", "shell")
    assert await plugin.before_tool_callback(tool=tool, tool_args={}, tool_context=ctx) is None
