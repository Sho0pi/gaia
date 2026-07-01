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
    """Resolve a ref (canonical id, display name, or 'channel:sender') to a canonical user id."""
    user = ctx.gaia.users.resolve_ref(ref)
    return user.id if user is not None else None


def _identity_from_ref(ref: str) -> tuple[str, str] | None:
    """A ``(channel, sender)`` if ``ref`` carries a contactable identity, else None.

    A bare phone number is treated as WhatsApp (the common case); ``channel:sender`` is split as
    given. A plain name (no digits) returns None - there's no way to message it.
    """
    from gaia.users import normalize_wa_number

    if ":" in ref:
        channel, _, sender = ref.partition(":")
        if channel == "whatsapp":
            jid = normalize_wa_number(sender)
            return ("whatsapp", jid) if jid else None
        return (channel, sender) if sender else None
    jid = normalize_wa_number(ref)
    return ("whatsapp", jid) if jid else None


def _roster(ctx: CommandContext) -> str:
    """Known users as ``id (name) [role]`` lines - so a name that doesn't resolve can be picked."""
    users = ctx.gaia.users.list()
    if not users:
        return "(no users yet)"
    return "\n".join(
        f"- {u.id} ({u.name or '?'}) [{u.role}]"
        for u in sorted(users, key=lambda x: (x.role, x.id))
    )


class UserCommand(Command):
    name = "user"
    capability = "manage_users"

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
    aliases = ("role",)

    async def run(self, ctx: CommandContext) -> str:
        # Role is the LAST word so the ref may contain spaces (a phone number like '+972 50 123').
        ref, _, role = ctx.args.rpartition(" ")
        ref, role = ref.strip(), role.strip().lower()
        if not ref or role not in _ROLES:
            return f"Usage: /approve <id|name|channel:sender|number> <{'/'.join(_ROLES)}>"
        user_id = _find(ctx, ref)
        if user_id is None:
            # No match by id/name/identity. If the ref is a contactable identity (a phone number
            # or channel:sender), onboard a brand-new person; otherwise show the roster so the
            # right id can be picked - e.g. a Hebrew display name that 'Ron' doesn't match.
            identity = _identity_from_ref(ref)
            if identity is None:
                return f"No one matches {ref!r}. Pick by id and try again:\n{_roster(ctx)}"
            channel, sender = identity
            existing = ctx.gaia.users.resolve(channel, sender)
            if existing is None:
                added = ctx.gaia.users.register(channel, sender, name=ref, role=role)  # type: ignore[arg-type]
                return f"Added {added.id} as {added.role}."
            user_id = existing.id
        updated = ctx.gaia.users.set_role(user_id, role)  # type: ignore[arg-type]
        assert updated is not None
        return f"{updated.id} is now {updated.role}."


class RemoveCommand(Command):
    name = "remove"
    capability = "manage_users"
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

    async def run(self, ctx: CommandContext) -> str:
        user_id, _, ident = ctx.args.partition(" ")
        channel, _, sender = ident.strip().partition(":")
        if not user_id or not channel or not sender:
            return "Usage: /link <id> <channel:sender>"
        updated = ctx.gaia.users.link(user_id.strip(), channel, sender)
        if updated is None:
            return f"No user {user_id.strip()!r} (try /user)."
        return f"Linked {channel}:{sender} → {updated.id}."
