"""Shared contract every connector speaks.

A connector is a dumb pipe: it hands inbound message text to a ``Handler``
coroutine together with a ``Send`` callback, and the handler pushes each reply
back through ``Send``. Streaming each reply (rather than returning one string)
lets a single inbound message produce several outbound ones. Defining the aliases
once here keeps telegram/whatsapp/whatsapp_web (and the God glue) in agreement
instead of each redeclaring them.

The ``ask`` tool needs to surface a *question with choices*, which is richer than a
text reply. Rather than widen ``Send`` (and every connector with it), a connector
that can render choices natively attaches an :data:`AskSend` callback as ``send.ask``;
the handler uses it when present and otherwise falls back to :func:`render_ask_text`,
the plain-text numbered-list floor every connector gets for free.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

# Sink a connector provides; the handler calls it once per reply message.
Send = Callable[[str], Awaitable[None]]

# Receives inbound text + the sink, streams replies through it, returns nothing.
Handler = Callable[[str, Send], Awaitable[None]]


@dataclass(frozen=True)
class Ask:
    """A clarifying question the agent wants the user to answer.

    Mirrors the ``ask`` tool's call arguments. ``options`` empty means a free-text
    question; otherwise a connector may render a picker. ``ask_id`` correlates the
    question with its rendering (e.g. button callback data).
    """

    question: str
    options: list[str]
    option_descriptions: list[str] | None
    multi_select: bool
    ask_id: str


# Richer sink a connector MAY provide alongside Send (attached as ``send.ask``) when it
# can render choices natively; absent it, the handler uses render_ask_text + Send.
AskSend = Callable[[Ask], Awaitable[None]]


def render_ask_text(ask: Ask) -> str:
    """Plain-text floor for an :class:`Ask`: the question, then numbered options.

    Every connector can show this; native pickers (CLI widget, chat buttons) are an
    optional upgrade. With options, the user replies with a number (or, when
    ``multi_select``, comma-separated numbers); without options it's a free-text ask.
    """
    if not ask.options:
        return ask.question

    lines = [ask.question]
    descriptions = ask.option_descriptions or []
    for i, option in enumerate(ask.options, start=1):
        gloss = descriptions[i - 1] if i - 1 < len(descriptions) else ""
        lines.append(f"{i}. {option}" + (f" — {gloss}" if gloss else ""))
    hint = (
        "Reply with the numbers (comma-separated)."
        if ask.multi_select
        else "Reply with the number of your choice."
    )
    lines.append(hint)
    return "\n".join(lines)


def resolve_option_reply(ask: Ask, text: str) -> str:
    """Map a numbered text reply back to option label(s); pass free text through.

    For a numbered-list rendering (:func:`render_ask_text`), the user answers with a
    number (or comma-separated numbers when ``multi_select``). This turns "2" into the
    second option's label so the resumed agent sees the choice, not a bare index. Any
    reply that isn't a clean in-range selection is returned unchanged and treated as a
    free-text answer.
    """
    if not ask.options:
        return text

    raw = text.replace(",", " ").split()
    if not raw or not all(tok.isdigit() for tok in raw):
        return text
    indices = [int(tok) for tok in raw]
    if any(n < 1 or n > len(ask.options) for n in indices):
        return text
    if not ask.multi_select and len(indices) != 1:
        return text
    return ", ".join(ask.options[n - 1] for n in indices)
