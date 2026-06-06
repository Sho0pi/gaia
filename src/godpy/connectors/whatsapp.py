"""WhatsApp adapter built on pywa (Meta WhatsApp Cloud API).

Deferred import keeps the module importable without the pywa dep installed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

Handler = Callable[[str], Awaitable[str]]


class WhatsAppConnector:
    """Bridges WhatsApp Cloud API messages to a God handler coroutine."""

    def __init__(self, phone_id: str, token: str, handler: Handler) -> None:
        self._phone_id = phone_id
        self._token = token
        self._handler = handler

    def build_client(self) -> object:
        """Create a pywa client wired to the handler."""
        from pywa import WhatsApp
        from pywa.types import Message

        wa = WhatsApp(phone_id=self._phone_id, token=self._token)

        @wa.on_message  # type: ignore[untyped-decorator]
        async def _on_message(_client: WhatsApp, message: Message) -> None:
            if message.text:
                reply = await self._handler(message.text)
                message.reply_text(reply)

        return wa
