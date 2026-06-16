"""Access control: capability groups + pure resolution of who-may-call-what.

:mod:`gaia.acl.groups` is the capability model (group -> tool ids, role defaults);
:mod:`gaia.acl.resolve` is the pure set math (role U grants minus denies). Enforcement lives
in :class:`gaia.core.plugins.ToolPermissionPlugin` (the hard gate) and the toolset filter
in :mod:`gaia.core.agent` / :mod:`gaia.agents.factory`.
"""

from __future__ import annotations

from gaia.acl.groups import ALL, DEFAULT_ROLE_CAPS, GROUPS, MANAGE_USERS
from gaia.acl.resolve import (
    allowed_tool_ids,
    can,
    effective_capabilities,
    role_capabilities,
)

__all__ = [
    "ALL",
    "DEFAULT_ROLE_CAPS",
    "GROUPS",
    "MANAGE_USERS",
    "allowed_tool_ids",
    "can",
    "effective_capabilities",
    "role_capabilities",
]
