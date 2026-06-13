"""The ``message_user`` tool: send a message to a specific person, proactively.

Unlike a normal reply (which streams back to whoever is talking to Gaia), this actively
delivers text to *another* user on a live connector — the piece that makes "in 5 minutes
text Grace 'I love you'" actually reach Grace. The recipient is resolved through the user
store (by canonical id like ``grace``, by display name, or by a ``channel:sender`` /
raw phone), so the model can name a person and let the store supply the address.

Root-only and bound to the live :class:`~gaia.core.agent.Gaia` (like ``delegate_to_soul``)
because it needs the running connector registry (``gaia.connectors``) to send. Outside the
daemon that registry is empty, so the tool returns a clear error instead of silently
dropping — connectors only exist while ``gaia serve`` runs.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from gaia.connectors.base import current_chat

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.users import User, UserStore

#: Tool id / ADK tool name (matches the closure name).
NAME = "message_user"


def _infer_channel(connectors: dict[str, Any], channel: str) -> str:
    """Pick the channel to send on when the model didn't name one.

    Order: explicit arg → the current conversation's channel → the only live connector
    (the common case: a single-connector daemon, e.g. just whatsapp). Empty when it's
    genuinely ambiguous (two+ connectors live and no other hint).
    """
    if channel:
        return channel
    ambient = current_chat.get()[0]
    if ambient:
        return ambient
    live = list(connectors)
    return live[0] if len(live) == 1 else ""


def _normalize_chat(channel: str, recipient: str) -> str:
    """Turn a raw recipient into a chat id the connector's ``send_to`` accepts.

    For whatsapp a phone is wanted, so strip formatting (spaces, dashes, parens, a
    leading ``+``) down to digits; an already-qualified JID (``...@server``) or a
    non-whatsapp id passes through untouched.
    """
    if "@" in recipient:
        return recipient
    if channel == "whatsapp":
        digits = re.sub(r"\D", "", recipient)
        return digits or recipient
    return recipient


def _resolve_target(
    store: UserStore, connectors: dict[str, Any], recipient: str, channel: str
) -> tuple[str, str] | str:
    """Resolve ``recipient`` to a ``(channel, chat)`` to send to, or an error string.

    Tries the user store first (canonical id, then display name, then a
    ``channel:sender`` identity); failing that, treats ``recipient`` as a raw sender id
    (a phone/JID), inferring the channel from the arg, the live chat, or the only
    running connector.
    """
    user: User | None = store.get(recipient)
    if user is None:
        user = next((u for u in store.list() if u.name.lower() == recipient.lower()), None)
    if user is None and ":" in recipient:
        ch, _, sender = recipient.partition(":")
        user = store.resolve(ch, sender)

    if user is not None:
        idents = user.identities
        if channel:
            match = next((i for i in idents if i.startswith(f"{channel}:")), None)
        else:
            match = idents[0] if idents else None
        if match is None:
            where = f" on {channel}" if channel else ""
            return f"{user.id!r} has no known address{where} (identities: {idents or 'none'})"
        ch, _, chat = match.partition(":")
        return ch, chat

    # Not a known user — treat recipient as a raw sender id (phone/JID).
    ch = _infer_channel(connectors, channel)
    if not ch:
        live = ", ".join(connectors) or "none"
        return f"can't tell which channel to send {recipient!r} on (live: {live}) — name one"
    return ch, _normalize_chat(ch, recipient)


def make_message_user(
    users: UserStore, connectors: dict[str, Any]
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the root-only ``message_user`` tool.

    Takes just the two services it needs — the user store and the live connector
    registry — rather than the whole ``Gaia`` (smaller surface, trivially testable).
    """

    async def message_user(recipient: str, text: str, channel: str = "") -> dict[str, Any]:
        """Send a text message to another person, now.

        Resolves ``recipient`` (a known user's id/name, or a raw phone/sender id) to their
        address and delivers ``text`` over the live connector. Use this to proactively
        reach someone — e.g. a scheduled "text Grace 'on my way'". Returns an error when
        the recipient can't be resolved or the channel isn't currently running.

        Args:
            recipient: who to message — a user id ("grace"), display name ("Grace"),
                a "channel:sender" id, or a raw phone / chat id.
            text: the message to send.
            channel: optional connector to send on (whatsapp/telegram); inferred from the
                recipient or the current conversation when omitted.
        """
        # No self-logging: ToolLoggingPlugin records one tool_used event per call.
        if not text.strip():
            return {"status": "error", "error_message": "text must not be empty"}

        resolved = _resolve_target(users, connectors, recipient.strip(), channel.strip())
        if isinstance(resolved, str):
            return {"status": "error", "error_message": resolved}
        ch, chat = resolved

        sender = connectors.get(ch)
        if sender is None:
            return {
                "status": "error",
                "error_message": f"channel {ch!r} is not running — can't deliver "
                "(start the daemon)",
            }

        try:
            await sender.send_to(chat, text)
        except Exception as exc:  # tools never raise to the model
            return {"status": "error", "error_message": f"delivery failed: {exc}"}

        return {"status": "success", "channel": ch, "chat": chat}

    return message_user
