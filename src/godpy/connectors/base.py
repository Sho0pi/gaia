"""Shared contract every connector speaks.

A connector is a dumb pipe: it hands inbound message text to a ``Handler``
coroutine together with a ``Send`` callback, and the handler pushes each reply
back through ``Send``. Streaming each reply (rather than returning one string)
lets a single inbound message produce several outbound ones. Defining the aliases
once here keeps telegram/whatsapp/whatsapp_web (and the God glue) in agreement
instead of each redeclaring them.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

# Sink a connector provides; the handler calls it once per reply message.
Send = Callable[[str], Awaitable[None]]

# Receives inbound text + the sink, streams replies through it, returns nothing.
Handler = Callable[[str, Send], Awaitable[None]]
