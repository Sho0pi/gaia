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


@dataclass(frozen=True)
class Media:
    """A non-text reply: a file on disk (e.g. a screenshot PNG) plus a caption."""

    path: Path
    caption: str = ""


# What the handler may hand a connector to send back: plain text or a media file.
Reply = str | Media

# Sink a connector provides; the handler calls it once per reply (text or media).
Send = Callable[[Reply], Awaitable[None]]

# Receives inbound text + the sink, streams replies through it, returns nothing.
Handler = Callable[[str, Send], Awaitable[None]]

# What a connector calls per inbound message: it identifies the *sender*
# (``sender_id`` = the channel-specific id, ``name`` = a display name from the channel)
# and the text, plus the reply sink. The channel is bound when the connector is built,
# so the connector only supplies who-and-what; the dispatcher resolves the sender to a
# canonical user + role, gates guests, and routes to that user's handler.
Dispatch = Callable[[str, str, str, Send], Awaitable[None]]


def as_text(reply: Reply) -> str:
    """Best-effort text form of a reply, for connectors that can't send media.

    A text reply passes through; a :class:`Media` reply degrades to its caption (or
    the file path) so the user at least gets told what was produced.
    """
    if isinstance(reply, Media):
        return reply.caption or str(reply.path)
    return reply
