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

from google.adk.tools.tool_context import ToolContext

from gaia import constants
from gaia.connectors.base import current_chat
from gaia.tools._helpers import err, ok

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.memory import Mem0MemoryService
    from gaia.users import User, UserStore

#: A phone-ish run of digits (with common separators); kept only if >= 8 digits so years,
#: task ids, and other short numbers in memory aren't mistaken for a number.
# ponytail: memory contact-resolution is phone/WhatsApp-only for now; channel-agnostic
# contacts (telegram usernames, email, …) tracked in #206.
_PHONE_RE = re.compile(r"\+?\d[\d\s().\-]{6,}\d")

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


def user_address(store: UserStore, user_id: str, channel: str = "") -> tuple[str, str] | None:
    """A known user's ``(channel, chat)`` to reach them, or ``None``.

    Picks the identity on ``channel`` when given, else the user's first identity. Shared by
    the message tool and the missions notifier (push a result to a task's owner).
    """
    user = store.get(user_id)
    if user is None or not user.identities:
        return None
    if channel:
        match = next((i for i in user.identities if i.startswith(f"{channel}:")), None)
    else:
        match = user.identities[0]
    if match is None:
        return None
    ch, _, chat = match.partition(":")
    return ch, chat


def _resolve_target(
    store: UserStore, connectors: dict[str, Any], recipient: str, channel: str
) -> tuple[str, str] | str | None:
    """Resolve ``recipient`` to a ``(channel, chat)``, an error string, or ``None``.

    Tries the user store first (canonical id, then display name, then a ``channel:sender``
    identity), then a raw sender id (a phone/JID). Returns ``None`` for a bare name/label
    that is neither a known user nor an address (e.g. "girlfriend") so the caller can try
    resolving it against the user's memory.
    """
    user: User | None = store.get(recipient)
    if user is None:
        user = next((u for u in store.list() if u.name.lower() == recipient.lower()), None)
    if user is None and ":" in recipient:
        ch, _, sender = recipient.partition(":")
        user = store.resolve(ch, sender)

    if user is not None:
        addr = user_address(store, user.id, channel)
        if addr is None:
            where = f" on {channel}" if channel else ""
            return (
                f"{user.id!r} has no known address{where} (identities: {user.identities or 'none'})"
            )
        return addr

    # A bare name/label (no JID, no digits) that isn't a known user — not an address.
    # Signal the caller to look it up in the user's memory (e.g. "girlfriend" -> a number).
    if "@" not in recipient and not any(char.isdigit() for char in recipient):
        return None

    # Otherwise treat recipient as a raw sender id (phone/JID).
    ch = _infer_channel(connectors, channel)
    if not ch:
        live = ", ".join(connectors) or "none"
        return f"can't tell which channel to send {recipient!r} on (live: {live}) — name one"
    return ch, _normalize_chat(ch, recipient)


def _phone_numbers(text: str) -> list[str]:
    """Digit-only phone numbers found in ``text`` (>= 8 digits), in order, de-duped."""
    found: list[str] = []
    for match in _PHONE_RE.findall(text):
        digits = re.sub(r"\D", "", match)
        if len(digits) >= 8 and digits not in found:
            found.append(digits)
    return found


async def _resolve_via_memory(
    memory: Mem0MemoryService | None,
    caller_id: str,
    recipient: str,
    connectors: dict[str, Any],
    channel: str,
) -> tuple[str, str] | str:
    """Resolve a label like "girlfriend" to a ``(channel, number)`` from the caller's memory.

    Auto-uses the number only when memory yields exactly one; otherwise returns an error the
    model can relay (ask for the number, or disambiguate).
    """
    if memory is None or not caller_id:
        return (
            f"I don't have a contact named {recipient!r}. Give me their number or save them first."
        )
    try:
        response = await memory.search_memory(
            app_name=constants.APP_NAME, user_id=caller_id, query=recipient
        )
    except Exception:
        return f"couldn't look up {recipient!r} in memory — give me their number."
    numbers: list[str] = []
    for entry in response.memories:
        parts = entry.content.parts if entry.content else None
        hit_text = "".join(part.text for part in (parts or []) if part.text)
        for number in _phone_numbers(hit_text):
            if number not in numbers:
                numbers.append(number)
    if not numbers:
        return f"I don't know {recipient!r}'s number — nothing in memory. Tell me the number first."
    if len(numbers) > 1:
        return f"I found more than one number for {recipient!r}: {', '.join(numbers)} — which one?"
    ch = _infer_channel(connectors, channel)
    if not ch:
        live = ", ".join(connectors) or "none"
        return f"can't tell which channel to send {recipient!r} on (live: {live}) — name one"
    return ch, _normalize_chat(ch, numbers[0])


def make_message_user(
    users: UserStore,
    connectors: dict[str, Any],
    memory: Callable[[], Mem0MemoryService | None],
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the root-only ``message_user`` tool.

    Takes the user store, the live connector registry, and a getter for the long-term
    memory service (so a recipient named by relationship/nickname — "girlfriend" — can be
    resolved to a number from the caller's memory) rather than the whole ``Gaia``.
    """

    async def message_user(
        recipient: str, text: str, channel: str = "", *, tool_context: ToolContext
    ) -> dict[str, Any]:
        """Proactively send a text to another person now (e.g. "text Grace 'on my way'").
        Returns an error if the recipient can't be resolved (then ask for a number) or the
        channel isn't running.

        Args:
            recipient: a user id/name ("grace"), a relationship/nickname in your memory
                ("girlfriend"), a "channel:sender" id, or a raw phone/chat id.
            channel: connector to send on (whatsapp/telegram); inferred when omitted.
        """
        # No self-logging: ToolLoggingPlugin records one tool_used event per call.
        if not text.strip():
            return err("text must not be empty")

        resolved = _resolve_target(users, connectors, recipient.strip(), channel.strip())
        if resolved is None:  # a bare label — resolve it from the caller's memory
            caller_id = getattr(tool_context, "user_id", "") or ""
            resolved = await _resolve_via_memory(
                memory(), caller_id, recipient.strip(), connectors, channel.strip()
            )
        if isinstance(resolved, str):
            return err(resolved)
        ch, chat = resolved

        sender = connectors.get(ch)
        if sender is None:
            return err(f"channel {ch!r} is not running — can't deliver (start the daemon)")

        try:
            await sender.send_to(chat, text)
        except Exception as exc:  # tools never raise to the model
            return err(f"delivery failed: {exc}")

        return ok(channel=ch, chat=chat)

    return message_user
