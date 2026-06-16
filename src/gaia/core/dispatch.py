"""Resolve an inbound sender to a user, gate guests, route to that user's handler.

Connectors are dumb pipes: they know *who* sent *what* on *which* channel, not *which
gaia user* that is. The :class:`Dispatcher` sits between them and the per-user
:class:`~gaia.core.handler.GaiaHandler`s:

1. resolve ``(channel, sender_id)`` to a :class:`~gaia.users.User` (registering an
   unknown sender at the channel's ``default_role``);
2. block ``guest`` senders with a short notice — they never reach the model or memory;
3. hand the turn to a handler built with ``user_id=user.id`` (so memory partitions per
   person, shared across that person's channels), cached per ``(user, channel)``.

One ``Dispatcher`` per process; each connector is handed a channel-bound
:data:`~gaia.connectors.base.Dispatch` callable that forwards into :meth:`dispatch`.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import TYPE_CHECKING

from gaia.connectors.base import Dispatch, Send
from gaia.core.handler import GaiaHandler, build_handler

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia
    from gaia.users import Role, User

logger = logging.getLogger(__name__)


class Dispatcher:
    """Routes inbound messages to per-user handlers, gating unapproved guests."""

    def __init__(self, gaia: Gaia) -> None:
        self._gaia = gaia
        self._handlers: dict[tuple[str, str], GaiaHandler] = {}

    def for_channel(self, channel: str) -> Dispatch:
        """A :data:`Dispatch` bound to ``channel`` for one connector to call per message."""
        return partial(self._dispatch, channel)

    async def flush_all(self) -> None:
        """Drain every live per-user handler's memory buffer (best-effort, on shutdown)."""
        for handler in list(self._handlers.values()):
            await handler.flush()

    async def invalidate_user(self, user_id: str) -> None:
        """Drop ``user_id``'s cached handlers so their next message rebuilds fresh.

        ADK has no API to hot-swap a live agent's tool list, and each handler caches its
        Runner (with the ACL-filtered toolset + prompt baked in at build). So when a
        user's capabilities change (``/grant`` / ``/revoke``), the only way the filter and
        prompt pick it up is to rebuild — we flush each stale handler's memory buffer, then
        evict it. The next turn builds a new handler with the current permissions. (The
        hard gate already reflects the change immediately; this refreshes the UX layer.)
        """
        for key in [k for k in self._handlers if k[0] == user_id]:
            handler = self._handlers.pop(key)
            await handler.flush()

    async def _dispatch(
        self, channel: str, sender_id: str, name: str, text: str, send: Send
    ) -> None:
        users = self._gaia.users
        user = users.resolve(channel, sender_id)
        if user is None:
            user = users.register(channel, sender_id, name, role=self._default_role(channel))
            logger.info(
                "new sender %s:%s registered as %r (%s)", channel, sender_id, user.id, user.role
            )

        if user.role == "guest":
            # Silently drop guest messages — the model never sees them and nothing
            # goes back over the wire. Approval is out-of-band: the guest reaches the
            # admin directly (DM, etc), the admin promotes them via the user command.
            logger.info(
                "dropped message from guest %s:%s (id=%s) — awaiting admin approval",
                channel,
                sender_id,
                user.id,
            )
            return

        await self._handler_for(user, channel)(text, send)

    def _handler_for(self, user: User, channel: str) -> GaiaHandler:
        """The cached handler for this person on this channel (built on first use).

        Keyed on ``user.id`` so memory (mem0 ``user_id``) is shared across the person's
        channels; ``session_id`` includes the channel so concurrent channels for one
        person don't interleave a single ADK session.
        """
        key = (user.id, channel)
        if key not in self._handlers:
            self._handlers[key] = build_handler(
                self._gaia,
                user_id=user.id,
                session_id=f"{user.id}:{channel}",
                role=user.role,
            )
        return self._handlers[key]

    def _default_role(self, channel: str) -> Role:
        """The role a brand-new sender on ``channel`` gets — the connector's ``default_role``.

        Remote channels default to ``guest`` (gated until approved). The local cli operator
        owns the machine, so it is **always** ``admin`` — not configurable, so a mis-set
        ``connectors.cli.default_role`` can never lock the owner out of their own terminal.
        Falls back to ``guest`` for an unconfigured/unknown channel.
        """
        if channel == "cli":  # CLIConnector.NAME — the trusted local operator
            return "admin"
        connectors = self._gaia.config.connectors
        cfg = getattr(connectors, channel, None)
        role = getattr(cfg, "default_role", "guest")
        return role if role in ("admin", "user", "guest") else "guest"  # type: ignore[return-value]


def build_dispatcher(gaia: Gaia) -> Dispatcher:
    """Return the process-wide :class:`Dispatcher` for ``gaia``.

    One instance per ``Gaia`` (owned by the facade), so every connector launcher and the
    ACL commands share a single per-user handler cache — invalidation on a grant reaches
    the same handlers that serve messages.
    """
    return gaia.dispatcher  # type: ignore[no-any-return]
