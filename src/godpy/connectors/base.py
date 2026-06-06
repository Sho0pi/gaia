"""Shared contract every connector speaks.

A connector is a dumb pipe: it hands inbound message text to a ``Handler``
coroutine and sends the returned string back to the user. Defining the alias once
here keeps telegram/whatsapp/whatsapp_web (and the God glue) in agreement instead
of each redeclaring it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

Handler = Callable[[str], Awaitable[str]]
