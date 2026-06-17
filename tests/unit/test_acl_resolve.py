"""Pure ACL resolution: role defaults, group expansion, grants/denies, wildcard."""

from __future__ import annotations

from types import SimpleNamespace

from gaia.acl import (
    ALL,
    MANAGE_USERS,
    allowed_tool_ids,
    can,
    effective_capabilities,
    role_capabilities,
)
from gaia.users.store import User

# A representative slice of the live registry ids.
ALL_IDS = {
    "web_fetch",
    "web_search",
    "remember",
    "load_memory",
    "exec",
    "exec_poll",
    "fs_read",
    "task_create",
    "browser_navigate",
}


def _user(role: str = "user", **kw: object) -> User:
    return User(id="u", role=role, **kw)  # type: ignore[arg-type]


def test_role_default_used_when_no_override() -> None:
    assert role_capabilities("user", None) == [
        "web",
        "memory",
        "browser",
        "tasks",
        "serve",
        "images",
    ]
    assert role_capabilities("admin", None) == [ALL]
    assert role_capabilities("guest", None) == []


def test_role_override_from_config() -> None:
    cfg = SimpleNamespace(roles={"user": SimpleNamespace(capabilities=["web"])})
    assert role_capabilities("user", cfg) == ["web"]


def test_admin_wildcard_gets_every_tool() -> None:
    assert allowed_tool_ids(_user(role="admin"), None, ALL_IDS) == ALL_IDS


def test_user_default_excludes_shell() -> None:
    allowed = allowed_tool_ids(_user(), None, ALL_IDS)
    assert "web_fetch" in allowed and "remember" in allowed
    assert "exec" not in allowed and "fs_read" not in allowed


def test_grant_group_adds_tools() -> None:
    allowed = allowed_tool_ids(_user(grants=["shell"]), None, ALL_IDS)
    assert {"exec", "exec_poll"} <= allowed


def test_grant_raw_tool_id() -> None:
    allowed = allowed_tool_ids(_user(grants=["fs_read"]), None, ALL_IDS)
    assert "fs_read" in allowed
    assert "fs_write" not in allowed  # only the one raw id


def test_deny_group_removes_role_default() -> None:
    allowed = allowed_tool_ids(_user(denies=["web"]), None, ALL_IDS)
    assert "web_fetch" not in allowed and "web_search" not in allowed
    assert "remember" in allowed  # other defaults stay


def test_deny_beats_grant() -> None:
    allowed = allowed_tool_ids(_user(grants=["shell"], denies=["exec"]), None, ALL_IDS)
    assert "exec" not in allowed
    assert "exec_poll" in allowed  # rest of the shell group survives


def test_none_user_is_trusted() -> None:
    assert allowed_tool_ids(None, None, ALL_IDS) == ALL_IDS
    assert effective_capabilities(None, None) == {ALL}


def test_can_manage_users() -> None:
    assert can(_user(role="admin"), MANAGE_USERS, None) is True
    assert can(_user(role="user"), MANAGE_USERS, None) is False
    assert can(_user(role="user", grants=[MANAGE_USERS]), MANAGE_USERS, None) is True
    assert can(_user(role="admin", denies=[MANAGE_USERS]), MANAGE_USERS, None) is False


def test_unknown_capability_expands_to_nothing() -> None:
    allowed = allowed_tool_ids(_user(role="guest", grants=["bogus"]), None, ALL_IDS)
    assert allowed == set()
