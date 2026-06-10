"""Regular-account WhatsApp adapter built on neonize (whatsmeow bindings).

Unlike the Cloud-API :class:`~godpy.connectors.whatsapp.WhatsAppConnector`, this
talks the WhatsApp-Web multidevice protocol: a personal account pairs by scanning
a QR code printed to the terminal on first run, and the session is persisted to a
local SQLite db so subsequent runs reconnect without a new scan.

Deferred import keeps the module importable without the neonize dep installed, so
unit tests can exercise the wiring without the native whatsmeow binary.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from godpy.connectors.base import Handler, Media, Reply

if TYPE_CHECKING:  # pragma: no cover - typing only
    from neonize.aioze.client import NewAClient

logger = logging.getLogger(__name__)


def patch_protobuf_version_guard() -> None:
    """Make protobuf's gencode/runtime version guard a no-op. Call before importing neonize.

    neonize 0.3.18 ships protobuf-7.34 *gencode*, but google-adk / mem0ai / a2a-sdk
    pin the protobuf *runtime* to <7. The generated ``*_pb2`` modules call
    ``ValidateProtobufRuntimeVersion`` at import and refuse to load when the runtime
    is older than the gencode — even though whatsmeow's messages use only baseline
    wire features the older runtime decodes fine. Neutralising the *version check*
    (not the wire format) lets neonize and the rest of godpy share one interpreter.
    The clean long-term fix is to run neonize as a sidecar with its own deps (#5).
    """
    from google.protobuf import runtime_version

    runtime_version.ValidateProtobufRuntimeVersion = lambda *_a, **_k: None


def _message_text(message: Any) -> str:
    """Extract plain text from a neonize ``MessageEv`` (plain or extended)."""
    msg = message.Message
    return str(msg.conversation or msg.extendedTextMessage.text)


class WhatsAppWebConnector:
    """Bridges regular-account WhatsApp messages to a God handler coroutine."""

    def __init__(self, session_db: Path, handler: Handler) -> None:
        self._session_db = session_db
        self._handler = handler

    def build_client(self) -> NewAClient:
        """Create a neonize client wired to the handler.

        The session db's parent dir is created if missing. On first connect neonize
        prints a QR code to the terminal; ``PairStatusEv``/``ConnectedEv`` log the
        outcome so the operator knows when the scan succeeded.
        """
        patch_protobuf_version_guard()  # let neonize import under a protobuf<7 runtime

        from neonize.aioze.client import NewAClient
        from neonize.aioze.events import ConnectedEv, MessageEv, PairStatusEv

        self._session_db.parent.mkdir(parents=True, exist_ok=True)
        client = NewAClient(str(self._session_db))

        @client.event(ConnectedEv)  # type: ignore[untyped-decorator]
        async def _on_connected(_client: NewAClient, _event: ConnectedEv) -> None:
            logger.info("whatsapp connected")

        @client.event(PairStatusEv)  # type: ignore[untyped-decorator]
        async def _on_pair(_client: NewAClient, event: PairStatusEv) -> None:
            logger.info("whatsapp paired as %s", event.ID.User)

        @client.event(MessageEv)  # type: ignore[untyped-decorator]
        async def _on_message(client: NewAClient, message: MessageEv) -> None:
            text = _message_text(message)
            if text:
                chat = message.Info.MessageSource.Chat  # JID to send media replies to

                async def send(reply: Reply) -> None:
                    # An image reply goes out as a real WhatsApp image; text replies
                    # quote the inbound message as before.
                    if isinstance(reply, Media):
                        await client.send_image(
                            chat, str(reply.path), caption=reply.caption or None
                        )
                    else:
                        await client.reply_message(reply, message)

                await self._handler(text, send)

        return client

    async def start(self) -> None:
        """Connect (prompting a QR scan on first run) and block receiving events."""
        client = self.build_client()
        logger.info("whatsapp starting — scan the QR if prompted (session: %s)", self._session_db)
        await client.connect()
        await client.idle()  # blocks, keeps receiving events
