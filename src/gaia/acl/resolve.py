"""Pure ACL resolution: capabilities -> allowed tool ids / command rights.

No I/O, no ADK — just set math over the capability model in :mod:`gaia.acl.groups`, so
it unit-tests in isolation. The effective capability set of a user is::

    role defaults (or config override)  U  user.grants  minus  user.denies

applied at the *tool-id* level so a deny of a group or of a single tool both bite.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gaia.acl.groups import ALL, DEFAULT_ROLE_CAPS, GROUPS

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.config import GaiaConfig
    from gaia.users.store import Role, User


def role_capabilities(role: Role, config: GaiaConfig | None) -> list[str]:
    """The capabilities a role holds: the ``gaia.yaml`` override if set, else the default."""
    roles = getattr(config, "roles", None) or {}
    entry = roles.get(role)
    if entry is not None and entry.capabilities:
        return list(entry.capabilities)
    return list(DEFAULT_ROLE_CAPS.get(role, []))


def _expand(caps: set[str], all_tool_ids: set[str]) -> set[str]:
    """Expand capability tokens to concrete tool ids against the live registry ids."""
    if ALL in caps:
        return set(all_tool_ids)
    out: set[str] = set()
    for cap in caps:
        group = GROUPS.get(cap)
        if group is not None:
            # `group & all_tool_ids` is set intersection: the group's tool ids that are
            # actually present right now (a group may name tools not registered in this
            # config, e.g. browser when Playwright is absent). `out |= …` is set union:
            # accumulate them into the result. Equivalent to out.update(group & ids).
            out |= group & all_tool_ids
        elif cap in all_tool_ids:
            out.add(cap)  # a raw tool id (fine-grained per-user grant)
    return out


def effective_capabilities(user: User | None, config: GaiaConfig | None) -> set[str]:
    """The capability tokens a user holds: role defaults U grants minus denies.

    ``None`` user (caller that never resolved — cron / single-user / tests) is trusted
    with the wildcard, matching the handler's admin default.
    """
    if user is None:
        return {ALL}
    caps = set(role_capabilities(user.role, config)) | set(user.grants)
    return caps - set(user.denies)


def allowed_tool_ids(
    user: User | None, config: GaiaConfig | None, all_tool_ids: set[str]
) -> set[str]:
    """The concrete tool ids ``user`` may call, after grants and denies."""
    if user is None:
        return set(all_tool_ids)
    granted = _expand(set(role_capabilities(user.role, config)) | set(user.grants), all_tool_ids)
    denied = _expand(set(user.denies), all_tool_ids)
    return granted - denied


def can(user: User | None, capability: str, config: GaiaConfig | None) -> bool:
    """Whether ``user`` holds ``capability`` (a command right like ``manage_users``).

    Wildcard holders pass anything; an explicit deny of the capability removes it.
    """
    caps = effective_capabilities(user, config)
    if capability in set(user.denies if user else ()):
        return False
    return ALL in caps or capability in caps
