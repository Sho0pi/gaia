"""User identity: who is talking, across channels, and what they're allowed to do.

A :class:`User` is one person — a single canonical identity that may be reached through
several channel-specific ids (a whatsapp number, a telegram id, the local cli). Memory
and sessions are keyed by the canonical ``user.id``, so the same person shares memory
across channels while distinct people stay isolated. The :class:`UserStore` persists the
mapping to ``~/.gaia/users.json``.
"""

from __future__ import annotations

from gaia.users.store import Role, User, UserStore, qualify, slugify

__all__ = ["Role", "User", "UserStore", "qualify", "slugify"]
