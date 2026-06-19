"""Shared contract every connector speaks.

A connector is a dumb pipe: it hands inbound message text to a ``Handler``
coroutine together with a ``Send`` callback, and the handler pushes each reply
back through ``Send``. Streaming each reply (rather than returning one string)
lets a single inbound message produce several outbound ones. Defining the aliases
once here keeps telegram/whatsapp/whatsapp_web (and the Gaia glue) in agreement
instead of each redeclaring them.

A reply is usually text, but may be :class:`Media` — an image (or file) on disk —
so a tool that produces a file (e.g. ``browser_screenshot``) can be delivered as an
actual attachment. A connector that can't send media falls back to the path as text.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

#: Which (channel, chat id) the handler is currently serving. Connectors set this just
#: before invoking the handler, so a tool that schedules a *later* reply (the cron
#: tool) can capture where to deliver it — the god PR's ``UserInfo.ChatID`` in
#: contextvar form. ``("", "")`` outside any chat (TUI, tests).
current_chat: ContextVar[tuple[str, str]] = ContextVar("current_chat", default=("", ""))

#: Files the user attached to the turn currently being handled (e.g. an inbound image),
#: as paths in the file sandbox. The handler sets it per turn; ``delegate_to_soul`` copies
#: them into the chosen soul's workspace so the soul can use a real, relative file (embed an
#: image in a site) instead of an absolute path that won't serve. Empty outside a media turn.
inbound_attachments: ContextVar[tuple[Path, ...]] = ContextVar("inbound_attachments", default=())


@dataclass(frozen=True)
class Media:
    """A non-text reply: a file on disk (e.g. a screenshot PNG) plus a caption."""

    path: Path
    caption: str = ""


@dataclass(frozen=True)
class InboundMedia:
    """An inbound attachment a connector downloaded to disk (e.g. a WhatsApp image)."""

    path: Path
    mime: str  # e.g. "image/jpeg"
    kind: str = "image"  # image | audio | video | document — only "image" is consumed today


@dataclass(frozen=True)
class Inbound:
    """One inbound message: its text and/or its attachments.

    The inbound counterpart to the outbound :data:`Reply` union — a message can carry text,
    media, or both (an image with a caption). Connectors build it; the handler turns it into
    a model turn. Text-only connectors just set ``text``.
    """

    text: str = ""
    media: tuple[InboundMedia, ...] = ()


# What the handler may hand a connector to send back: plain text or a media file.
Reply = str | Media

# Sink a connector provides; the handler calls it once per reply (text or media).
Send = Callable[[Reply], Awaitable[None]]

# Receives an inbound message + the sink, streams replies through it, returns nothing.
Handler = Callable[["Inbound", Send], Awaitable[None]]

# What a connector calls per inbound message: it identifies the *sender* (``sender_id`` =
# the channel-specific id, ``name`` = a display name from the channel) and the message, plus
# the reply sink. The channel is bound when the connector is built, so the connector only
# supplies who-and-what; the dispatcher resolves the sender to a canonical user + role, gates
# guests, and routes to that user's handler.
Dispatch = Callable[[str, str, "Inbound", Send], Awaitable[None]]


def as_text(reply: Reply) -> str:
    """Best-effort text form of a reply, for connectors that can't send media.

    A text reply passes through; a :class:`Media` reply degrades to its caption (or
    the file path) so the user at least gets told what was produced.
    """
    if isinstance(reply, Media):
        return reply.caption or str(reply.path)
    return reply
