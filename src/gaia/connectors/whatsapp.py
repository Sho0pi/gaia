"""WhatsApp adapter built on pywa (Meta WhatsApp Cloud API).

Deferred import keeps the module importable without the pywa dep installed.
"""

from __future__ import annotations

from gaia.connectors.base import Dispatch, Reply, as_text


class WhatsAppConnector:
    """Bridges WhatsApp Cloud API messages to the dispatcher (per-sender identity).

    The inbound webhook server isn't wired yet (#3), so this connector's receive path is
    not exercised in the daemon; it carries the same dispatch contract as the others so it
    drops in once the webhook lands.
    """

    NAME = "whatsapp"

    def __init__(self, phone_id: str, token: str, dispatch: Dispatch) -> None:
        self._phone_id = phone_id
        self._token = token
        self._dispatch = dispatch  # channel-bound: (sender_id, name, text, send)

    def build_client(self) -> object:
        """Create a pywa client wired to the dispatcher."""
        from pywa import WhatsApp
        from pywa.types import Button, Message

        wa = WhatsApp(phone_id=self._phone_id, token=self._token)

        @wa.on_message  # type: ignore[untyped-decorator]
        async def _on_message(_client: WhatsApp, message: Message) -> None:
            if message.text:

                async def send(reply: Reply) -> None:
                    message.reply_text(
                        as_text(reply),
                        buttons=[
                            Button(title="Menu", callback_data="menu"),
                            Button(title="Help", callback_data="help"),
                        ],
                    )

                sender = message.from_user
                await self._dispatch(
                    sender.wa_id, getattr(sender, "name", "") or "", message.text, send
                )

        return wa
