"""The user store: ``~/.gaia/users.json``, mapping channel senders to canonical users.

A *user* is one person. Each carries a stable ``id`` (the canonical slug used as the ADK
/ mem0 ``user_id``), a display ``name``, a ``role`` (admin/user/guest), and the list of
channel-qualified ids that reach them (``telegram:123``, ``whatsapp:972…@s.whatsapp.net``,
``cli:local``). Resolving an inbound sender to a user is what gives per-person memory that
is shared across that person's channels.

Hybrid ownership: admins are *seeded* from ``gaia.yaml`` (``config.admin``); everyone else
is *learned* at first contact and managed by the admin's chat commands. JSON (not yaml)
because the store carries runtime state (new users, role changes) the daemon rewrites.
Writes are atomic (tmp + rename), the cron-store / agent-registry pattern.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from gaia import constants

#: The three roles. ``guest`` is the default for an unknown remote sender and is gated
#: (blocked from the model/memory) until an admin approves it to ``user``/``admin``.
Role = Literal["admin", "user", "guest"]


def qualify(channel: str, sender_id: str) -> str:
    """The channel-qualified id stored in ``User.identities`` (``"channel:sender"``)."""
    return f"{channel}:{sender_id}"


def slugify(name: str) -> str:
    """A filesystem/id-safe lowercase slug from a display name (``"Grace P." → "grace-p"``)."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "user"


class User(BaseModel):
    """One person: a canonical id + display name + role + the ids that reach them."""

    id: str  # canonical slug; this is the ADK/mem0 user_id (memory partition key)
    name: str = ""
    role: Role = "guest"
    identities: list[str] = Field(default_factory=list)  # ["channel:sender", …]
    # Per-user ACL on top of the role's default capabilities: extra capabilities granted
    # (e.g. "shell" for a user trusted with exec) and ones removed. Both hold capability
    # tokens — a group name, "*", or a raw tool id (see gaia.acl). Default empty keeps
    # existing users.json backward-compatible.
    grants: list[str] = Field(default_factory=list)
    denies: list[str] = Field(default_factory=list)


#: Annotation aliases: inside UserStore the name `list` is the method, not the builtin,
#: so annotations there must go through these module-level names.
UserList = list[User]
StrList = list[str]


class UserStore:
    """File-backed user store; one JSON array, atomically rewritten on every change."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else constants.USERS_FILE
        # Guards every read-modify-write below so a first-contact register / role change can't
        # lose an update — the store is one shared singleton but connectors (PTB, neonize) run in
        # their own threads, so the json read→write sequence must be atomic across them. RLock so
        # a guarded method can call another (e.g. seed_admins → register/set_role).
        self._lock = threading.RLock()

    def list(self) -> UserList:
        """Every stored user, file order (empty list when the file is missing)."""
        if not self._path.exists():
            return []
        raw = json.loads(self._path.read_text() or "[]")
        return [User.model_validate(item) for item in raw]

    def get(self, user_id: str) -> User | None:
        return next((u for u in self.list() if u.id == user_id), None)

    def resolve(self, channel: str, sender_id: str) -> User | None:
        """The user reachable at ``channel:sender_id``, or ``None`` if unknown."""
        ident = qualify(channel, sender_id)
        return next((u for u in self.list() if ident in u.identities), None)

    def resolve_ref(self, ref: str) -> User | None:
        """Resolve a free-form ref (canonical id, display name, or ``channel:sender``) to a user."""
        user = self.get(ref)
        if user is None:
            user = next((u for u in self.list() if u.name.lower() == ref.lower()), None)
        if user is None and ":" in ref:
            ch, _, sender = ref.partition(":")
            user = self.resolve(ch, sender)
        return user

    def has_admin(self) -> bool:
        """Whether any admin exists yet — drives first-contact bootstrap when none does."""
        return any(u.role == "admin" for u in self.list())

    def register(self, channel: str, sender_id: str, name: str, role: Role) -> User:
        """Create a new user for a first-seen sender and persist them.

        The canonical id is a slug of ``name`` (deduped with a numeric suffix when taken),
        falling back to the channel-qualified id when there's no usable name.
        """
        with self._lock:
            users = self.list()
            taken = {u.id for u in users}
            base = slugify(name) if name.strip() else slugify(qualify(channel, sender_id))
            user = User(
                id=_dedupe(base, taken),
                name=name.strip(),
                role=role,
                identities=[qualify(channel, sender_id)],
            )
            self._write([*users, user])
            return user

    def set_role(self, user_id: str, role: Role) -> User | None:
        """Change a user's role; returns the updated user (or ``None`` if unknown)."""
        return self._mutate(user_id, lambda u: u.model_copy(update={"role": role}))

    def grant(self, user_id: str, capability: str) -> User | None:
        """Add a capability to the user's ``grants`` (and clear it from ``denies``).

        Idempotent. Returns the updated user, or ``None`` if unknown.
        """

        def add(u: User) -> User:
            grants = u.grants if capability in u.grants else [*u.grants, capability]
            denies = [d for d in u.denies if d != capability]
            return u.model_copy(update={"grants": grants, "denies": denies})

        return self._mutate(user_id, add)

    def revoke(self, user_id: str, capability: str) -> User | None:
        """Remove a capability the user holds: drop it from ``grants`` and add to ``denies``.

        Adding to ``denies`` matters when the capability comes from the role default (not
        ``grants``) — an explicit deny is the only way to take it back. Returns the updated
        user, or ``None`` if unknown.
        """

        def remove(u: User) -> User:
            grants = [g for g in u.grants if g != capability]
            denies = u.denies if capability in u.denies else [*u.denies, capability]
            return u.model_copy(update={"grants": grants, "denies": denies})

        return self._mutate(user_id, remove)

    def remove(self, user_id: str) -> User | None:
        """Delete a user entirely; returns the removed user (or ``None`` if unknown).

        Forgets the person from the store — their identities no longer resolve, so a
        later message from any of them is treated as a brand-new (gated) sender. Their
        long-term memory (mem0, keyed on ``user.id``) is *not* touched here; clear that
        separately if needed.
        """
        with self._lock:
            users = self.list()
            removed = next((u for u in users if u.id == user_id), None)
            if removed is None:
                return None
            self._write([u for u in users if u.id != user_id])
            return removed

    def set_name(self, user_id: str, name: str) -> User | None:
        """Change a user's display name; returns the updated user (or ``None``)."""
        return self._mutate(user_id, lambda u: u.model_copy(update={"name": name.strip()}))

    def link(self, user_id: str, channel: str, sender_id: str) -> User | None:
        """Glue another channel id onto an existing user (idempotent). ``None`` if unknown.

        The id is first detached from any other user that claimed it, so an identity maps
        to exactly one person.
        """
        ident = qualify(channel, sender_id)
        with self._lock:
            users = self.list()
            target = next((u for u in users if u.id == user_id), None)
            if target is None:
                return None
            updated: UserList = []
            for u in users:
                idents = [i for i in u.identities if i != ident]  # detach from everyone else
                if u.id == user_id and ident not in idents:
                    idents.append(ident)
                updated.append(u.model_copy(update={"identities": idents}))
            self._write(updated)
            return self.get(user_id)

    def seed_admins(self, admin_ids: StrList) -> None:
        """Ensure each ``"channel:sender"`` in ``admin_ids`` maps to an admin user.

        Idempotent startup step: an already-known identity is promoted to admin in place;
        an unknown one creates an admin user (id slugged from the sender). Lets the owner
        declare themselves in ``gaia.yaml`` once and be admin on first contact.
        """
        for ident in admin_ids:
            channel, _, sender_id = ident.partition(":")
            if not sender_id:
                continue
            existing = self.resolve(channel, sender_id)
            if existing is not None:
                if existing.role != "admin":
                    self.set_role(existing.id, "admin")
                continue
            self.register(channel, sender_id, name=sender_id, role="admin")

    # -- internals -------------------------------------------------------------------

    def _mutate(self, user_id: str, fn: object) -> User | None:
        with self._lock:
            users = self.list()
            if not any(u.id == user_id for u in users):
                return None
            updated = [fn(u) if u.id == user_id else u for u in users]  # type: ignore[operator]
            self._write(updated)
            return self.get(user_id)

    def _write(self, users: UserList) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps([u.model_dump() for u in users], indent=2) + "\n")
        os.replace(tmp, self._path)  # atomic on POSIX


def _dedupe(base: str, taken: set[str]) -> str:
    """``base`` if free, else ``base-2``, ``base-3``, … — a stable unique id."""
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"
