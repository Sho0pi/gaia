"""Admin commands to manage users: list, approve/role, name, link.

These are the runtime half of the hybrid identity model — admins seed themselves in
``gaia.yaml``, then approve and organise everyone else from chat. All four are
admin-only; a non-admin gets a short refusal (the store is never mutated).
"""

from __future__ import annotations

from gaia.commands.base import Command, CommandContext

_ROLES = ("admin", "user", "guest")


def require_manage_users(ctx: CommandContext) -> str | None:
    """Refusal string unless the caller holds the ``manage_users`` capability, else ``None``.

    Admins hold it via the ``*`` wildcard; a non-admin can be granted it explicitly
    (``/grant <user> manage_users``). Replaces the old admin-only check.
    """
    from gaia.acl import MANAGE_USERS, can

    user = ctx.gaia.users.get(ctx.user_id)
    if user is None:
        # Unresolved caller (cron / single-user / cli / tests): fall back to the role on
        # the context — admins pass, everyone else is refused (the pre-ACL behaviour).
        return None if ctx.role == "admin" else "Only an admin can run that."
    if not can(user, MANAGE_USERS, ctx.gaia.config):
        return "Only an admin can run that."
    return None


def _find(ctx: CommandContext, ref: str) -> str | None:
    """Resolve a user ref (canonical id or 'channel:sender') to a canonical user id."""
    store = ctx.gaia.users
    if store.get(ref) is not None:
        return ref
    channel, _, sender = ref.partition(":")
    if sender:
        user = store.resolve(channel, sender)
        if user is not None:
            return user.id
    return None


class UsersCommand(Command):
    name = "user"
    capability = "manage_users"
    summary = "List known users, their roles, and the channels that reach them (admin)."

    async def run(self, ctx: CommandContext) -> str:
        users = ctx.gaia.users.list()
        if not users:
            return "No users yet."
        lines = []
        for u in sorted(users, key=lambda x: (x.role, x.id)):
            ids = ", ".join(u.identities) or "—"
            label = f"{u.name} " if u.name else ""
            lines.append(f"- {u.id} {label}[{u.role}] — {ids}")
        return "Users:\n" + "\n".join(lines)


class ApproveCommand(Command):
    name = "approve"
    capability = "manage_users"
    summary = "Set a user's role (approve a guest). Usage: /approve <id|channel:sender> <role>."
    usage = "<id|channel:sender> <role>"
    aliases = ("role",)

    async def run(self, ctx: CommandContext) -> str:
        ref, _, role = ctx.args.partition(" ")
        role = role.strip().lower()
        if not ref or role not in _ROLES:
            return f"Usage: /approve <id|channel:sender> <{'/'.join(_ROLES)}>"
        user_id = _find(ctx, ref.strip())
        if user_id is None:
            return f"No user matching {ref.strip()!r} (try /user)."
        updated = ctx.gaia.users.set_role(user_id, role)  # type: ignore[arg-type]
        assert updated is not None
        return f"{updated.id} is now {updated.role}."


class RemoveCommand(Command):
    name = "remove"
    capability = "manage_users"
    summary = "Delete a user from the store. Usage: /remove <id|channel:sender> (admin)."
    usage = "<id|channel:sender>"
    aliases = ("deluser",)

    async def run(self, ctx: CommandContext) -> str:
        ref = ctx.args.strip()
        if not ref:
            return "Usage: /remove <id|channel:sender>"
        user_id = _find(ctx, ref)
        if user_id is None:
            return f"No user matching {ref!r} (try /user)."
        if user_id == ctx.user_id:
            return "You can't remove yourself."
        removed = ctx.gaia.users.remove(user_id)
        assert removed is not None
        return f"Removed {removed.id} — they'll be treated as a new (gated) sender next time."


class NameCommand(Command):
    name = "name"
    capability = "manage_users"
    summary = "Set a user's display name. Usage: /name <id|channel:sender> <name>."
    usage = "<id|channel:sender> <name>"

    async def run(self, ctx: CommandContext) -> str:
        ref, _, name = ctx.args.partition(" ")
        if not ref or not name.strip():
            return "Usage: /name <id|channel:sender> <name>"
        user_id = _find(ctx, ref.strip())
        if user_id is None:
            return f"No user matching {ref.strip()!r} (try /user)."
        updated = ctx.gaia.users.set_name(user_id, name.strip())
        assert updated is not None
        return f"{updated.id} is now named {updated.name!r}."


class LinkCommand(Command):
    name = "link"
    capability = "manage_users"
    summary = "Attach another channel id to a user. Usage: /link <id> <channel:sender>."
    usage = "<id> <channel:sender>"

    async def run(self, ctx: CommandContext) -> str:
        user_id, _, ident = ctx.args.partition(" ")
        channel, _, sender = ident.strip().partition(":")
        if not user_id or not channel or not sender:
            return "Usage: /link <id> <channel:sender>"
        updated = ctx.gaia.users.link(user_id.strip(), channel, sender)
        if updated is None:
            return f"No user {user_id.strip()!r} (try /user)."
        return f"Linked {channel}:{sender} → {updated.id}."
