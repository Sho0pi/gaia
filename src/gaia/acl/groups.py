"""Capability groups: the single source of truth for what a *capability* grants.

A **capability** is a token a role or user holds. It is one of:

* a **group** name (:data:`GROUPS`) — expands to a set of tool ids (and, for
  ``manage_users``, a command right that maps to no tool),
* the wildcard ``"*"`` — every tool / every command right,
* a raw **tool id** (e.g. ``"exec"``) — that one tool, for fine per-user grants.

A group can also claim tools by **name prefix** (:data:`GROUP_PREFIXES`): ``browser``
owns every ``browser_*`` tool. That's how the browser cap governs Microsoft's
playwright-mcp tools, which attach as an MCP toolset (not the registry) and so can't be
enumerated here — any ``browser_*`` name they expose is caught dynamically.

Roles get a prebuilt capability set (:data:`DEFAULT_ROLE_CAPS`); a user may carry extra
``grants`` and ``denies`` on top (see :mod:`gaia.acl.resolve`). Groups are coarse on
purpose: a new tool joins a group here once and every role holding that group gets it,
so the ACL config never has to enumerate individual tools.
"""

from __future__ import annotations

from gaia.users.store import Role

#: Wildcard capability: every tool and every command right.
ALL = "*"

#: Command-only capability (no tool): the right to manage users / permissions. The
#: user-management commands (``/approve``, ``/grant``, …) gate on it.
MANAGE_USERS = "manage_users"

#: group name -> the tool ids it expands to. Tool ids match the registry
#: (``gaia.tools.registry``). ``manage_users`` maps to no tool (command right only).
GROUPS: dict[str, frozenset[str]] = {
    "web": frozenset({"web_fetch", "web_search"}),
    "memory": frozenset({"remember", "load_memory"}),
    # Native browser_* tools are listed for display (/acl); every browser_* name, native or
    # playwright-mcp, is also caught by the GROUP_PREFIXES rule below — so the browser cap
    # governs the whole surface without enumerating the mcp tools.
    "browser": frozenset(
        {
            "browser_navigate",
            "browser_snapshot",
            "browser_click",
            "browser_type",
            "browser_screenshot",
        }
    ),
    "files": frozenset({"fs_read", "fs_write", "fs_edit", "fs_glob", "fs_grep"}),
    "shell": frozenset({"exec", "exec_poll", "exec_kill", "exec_list"}),
    "tasks": frozenset({"task_create", "task_list", "task_get", "task_update", "task_complete"}),
    "serve": frozenset({"serve", "serve_stop", "serve_list"}),
    "images": frozenset({"generate_image"}),
    "media": frozenset({"download_media"}),  # download a video/audio from a link
    "cron": frozenset({"cron"}),
    "ask": frozenset({"ask_user"}),  # pause the run to ask the human (a choice / a credential)
    "core": frozenset({"capabilities"}),  # read-only: which commands/workspace/serve rules apply
    # admin right: the user-management commands + the set_communication_style tool (changing
    # Gaia's global voice is an admin action, so only admins' agents get that tool).
    MANAGE_USERS: frozenset({"set_communication_style"}),
    # install/manage skills: the /skill command + the save_skill tool ("learn & grow").
    "skills": frozenset({"save_skill"}),
}

#: Tool-name prefix -> the capability that governs every tool with that prefix. Lets a
#: group claim tools it can't enumerate (off-registry MCP tools): ``browser_*`` (native and
#: playwright-mcp) all fall under the ``browser`` cap. Checked in addition to GROUPS.
GROUP_PREFIXES: dict[str, str] = {"browser_": "browser"}


def known_capabilities() -> set[str]:
    """Every token ``/grant`` / ``/revoke`` accept: a group name, a tool id, or ``*``."""
    tools = {tool for members in GROUPS.values() for tool in members}
    return set(GROUPS) | tools | {ALL}


def capability_error(cap: str) -> str | None:
    """A helpful message when ``cap`` isn't a valid capability, else ``None`` (it is valid).

    Catches a typo / wrong name (e.g. ``reminder`` for ``cron``) that would otherwise be stored
    silently and grant nothing.
    """
    if cap in known_capabilities():
        return None
    groups = ", ".join(sorted(GROUPS))
    return f"Unknown capability {cap!r}. Valid: {groups} (or a tool id, or '{ALL}')."


#: Built-in capabilities each role holds before per-user grants. Overridable per-role in
#: ``gaia.yaml`` (``roles.<role>.capabilities``). ``guest`` holds nothing — guests are
#: dropped at dispatch anyway; this keeps them tool-less if that ever changes.
DEFAULT_ROLE_CAPS: dict[Role, list[str]] = {
    "guest": [],
    "user": [
        "web",
        "memory",
        "browser",
        "tasks",
        "serve",
        "skills",
        "ask",
        "core",
        "images",
        "media",
        "cron",  # schedule your own reminders/jobs — safe self-service
    ],
    "admin": [ALL],
}
