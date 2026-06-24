"""Admin commands to manage per-user ACL: grant / revoke capabilities, show permissions.

A *capability* is an ACL group (``web``, ``shell``, ``manage_users``…), the wildcard
``*``, or a raw tool id. Roles get a default capability set; these commands add per-user
grants on top (or deny one the role would otherwise give). Gated on ``manage_users`` —
admins hold it via ``*``; ``/perms`` with no arg is self-service for any user.
"""

from __future__ import annotations

from gaia.commands.base import Command, CommandContext
from gaia.commands.users import _find, require_manage_users


class AclCommand(Command):
    name = "acl"
    summary = "List the available ACL capability groups and the tools each grants."

    async def run(self, ctx: CommandContext) -> str:
        from gaia.acl.groups import ALL, DEFAULT_ROLE_CAPS, GROUPS

        lines = ["Capabilities you can /grant or set on a role:"]
        for name in sorted(GROUPS):
            tools = ", ".join(sorted(GROUPS[name])) or "(command right only)"
            lines.append(f"- {name}: {tools}")
        lines.append(f"- {ALL}: every tool + every command right (wildcard)")
        lines.append("")
        lines.append("Role defaults:")
        for role, caps in DEFAULT_ROLE_CAPS.items():
            lines.append(f"- {role}: {', '.join(caps) or '—'}")
        return "\n".join(lines)


class GrantCommand(Command):
    name = "grant"
    capability = "manage_users"
    summary = "Grant a user an ACL capability. Usage: /grant <id|channel:sender> <capability>."
    usage = "<id|channel:sender> <capability>"

    async def run(self, ctx: CommandContext) -> str:
        ref, _, cap = ctx.args.partition(" ")
        cap = cap.strip()
        if not ref or not cap:
            return "Usage: /grant <id|channel:sender> <capability> (e.g. shell, web, *)"
        user_id = _find(ctx, ref.strip())
        if user_id is None:
            return f"No user matching {ref.strip()!r} (try /user)."
        updated = ctx.gaia.users.grant(user_id, cap)
        assert updated is not None
        return f"Granted {cap!r} to {updated.id} (grants: {', '.join(updated.grants) or '—'})."


class RevokeCommand(Command):
    name = "revoke"
    capability = "manage_users"
    summary = "Revoke an ACL capability from a user. Usage: /revoke <id|channel:sender> <cap>."
    usage = "<id|channel:sender> <capability>"

    async def run(self, ctx: CommandContext) -> str:
        ref, _, cap = ctx.args.partition(" ")
        cap = cap.strip()
        if not ref or not cap:
            return "Usage: /revoke <id|channel:sender> <capability>"
        user_id = _find(ctx, ref.strip())
        if user_id is None:
            return f"No user matching {ref.strip()!r} (try /user)."
        updated = ctx.gaia.users.revoke(user_id, cap)
        assert updated is not None
        return f"Revoked {cap!r} from {updated.id} (denies: {', '.join(updated.denies) or '—'})."


class PermsCommand(Command):
    name = "perms"
    summary = "Show a user's effective ACL capabilities. Usage: /perms [id|channel:sender]."
    usage = "[id|channel:sender]"
    aliases = ("permissions",)

    async def run(self, ctx: CommandContext) -> str:
        from gaia.acl import effective_capabilities, role_capabilities

        ref = ctx.args.strip()
        if ref:  # inspecting someone else requires manage_users
            if refusal := require_manage_users(ctx):
                return refusal
            user_id = _find(ctx, ref)
            if user_id is None:
                return f"No user matching {ref!r} (try /user)."
        else:
            user_id = ctx.user_id
        user = ctx.gaia.users.get(user_id)
        if user is None:
            return f"No user {user_id!r}."
        role_caps = role_capabilities(user.role, ctx.gaia.config)
        effective = sorted(effective_capabilities(user, ctx.gaia.config))
        lines = [
            f"{user.id} [{user.role}]",
            f"  role caps: {', '.join(role_caps) or '—'}",
            f"  grants:    {', '.join(user.grants) or '—'}",
            f"  denies:    {', '.join(user.denies) or '—'}",
            f"  effective: {', '.join(effective) or '—'}",
        ]
        return "\n".join(lines)
