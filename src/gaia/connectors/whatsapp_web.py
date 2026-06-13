"""Regular-account WhatsApp adapter built on neonize (whatsmeow bindings).

Unlike the Cloud-API :class:`~gaia.connectors.whatsapp.WhatsAppConnector`, this
talks the WhatsApp-Web multidevice protocol: a personal account pairs by scanning
a QR code printed to the terminal on first run, and the session is persisted to a
local SQLite db so subsequent runs reconnect without a new scan.

Deferred import keeps the module importable without the neonize dep installed, so
unit tests can exercise the wiring without the native whatsmeow binary.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gaia import constants
from gaia.connectors.base import Dispatch, Media, Reply, current_chat

if TYPE_CHECKING:  # pragma: no cover - typing only
    from neonize.aioze.client import NewAClient

    from gaia.voice import Transcriber

logger = logging.getLogger(__name__)


def _jid_to_str(jid: Any) -> str:
    """Round-trippable ``user@server`` form of a neonize JID (for the cron store)."""
    user = getattr(jid, "User", "")
    server = getattr(jid, "Server", "")
    return f"{user}@{server}" if user else str(server)


def _deliverable_chat(source: Any) -> str:
    """The chat id later proactive sends should target.

    WhatsApp's LID addressing hides the phone number: ``Chat`` is then a ``…@lid``
    JID, and ``send_message`` to it vanishes silently. For DMs the proto carries the
    real phone-number JID in ``SenderAlt`` — prefer it; groups keep ``Chat`` (@g.us).
    """
    chat = source.Chat
    if getattr(chat, "Server", "") == "lid" and not getattr(source, "IsGroup", False):
        alt = getattr(source, "SenderAlt", None)
        if getattr(alt, "User", ""):
            return _jid_to_str(alt)
    return _jid_to_str(chat)


def _sender_jid(source: Any) -> str:
    """The phone-number JID of *who sent* the message — the identity key.

    For a DM this is the same person as the chat; the proto hides the number behind a
    ``…@lid`` ``Sender``, so prefer ``SenderAlt`` (the real number) when present. In a
    group the chat is shared but the sender is the individual, so always use the sender.
    """
    alt = getattr(source, "SenderAlt", None)
    if getattr(alt, "User", ""):
        return _jid_to_str(alt)
    sender = getattr(source, "Sender", None)
    return _jid_to_str(sender) if getattr(sender, "User", "") else _deliverable_chat(source)


def patch_protobuf_version_guard() -> None:
    """Make protobuf's gencode/runtime version guard a no-op. Call before importing neonize.

    neonize 0.3.18 ships protobuf-7.34 *gencode*, but google-adk / mem0ai / a2a-sdk
    pin the protobuf *runtime* to <7. The generated ``*_pb2`` modules call
    ``ValidateProtobufRuntimeVersion`` at import and refuse to load when the runtime
    is older than the gencode — even though whatsmeow's messages use only baseline
    wire features the older runtime decodes fine. Neutralising the *version check*
    (not the wire format) lets neonize and the rest of gaia share one interpreter.
    The clean long-term fix is to run neonize as a sidecar with its own deps (#5).
    """
    from google.protobuf import runtime_version

    runtime_version.ValidateProtobufRuntimeVersion = lambda *_a, **_k: None


def _plain_text(msg: Any) -> str:
    """Plain text of a neonize ``Message`` — its ``conversation`` or extended text."""
    if msg is None:
        return ""
    extended = getattr(msg, "extendedTextMessage", None)
    return str(getattr(msg, "conversation", "") or getattr(extended, "text", "") or "")


def _quoted_text(msg: Any) -> str:
    """Text of the message this one replies to (the quoted message), or ``""``.

    A WhatsApp reply arrives as an ``extendedTextMessage`` whose ``contextInfo`` carries
    the original under ``quotedMessage``. We read it defensively (``getattr``) so a
    non-reply — or a future proto shape — yields ``""`` instead of raising.
    """
    extended = getattr(msg, "extendedTextMessage", None)
    context = getattr(extended, "contextInfo", None)
    quoted = getattr(context, "quotedMessage", None)
    return _plain_text(quoted)


def _message_text(message: Any) -> str:
    """Extract the inbound text from a neonize ``MessageEv``.

    When the user *replies to* a previous message, the quoted message is included as
    context so Gaia sees what the user is referring to (otherwise it only got the new
    line and the reply made no sense).
    """
    msg = message.Message
    text = _plain_text(msg)
    quoted = _quoted_text(msg)
    if quoted:
        return f"[Replying to an earlier message: {quoted}]\n\n{text}"
    return text


class WhatsAppWebConnector:
    """Bridges regular-account WhatsApp messages to the dispatcher (per-sender identity).

    ``transcriber`` (a :class:`gaia.voice.Transcriber`) turns inbound voice notes into
    text for the handler; ``None`` means voice messages are ignored (prior behaviour).
    ``show_active`` makes Gaia *look active* while it works: it marks an inbound message
    read (blue tick) the moment it starts and shows the "typing…" (or "recording audio…")
    indicator for the duration of the turn. Best-effort — degrades silently.
    """

    #: Connector id used in cron job channel fields / the daemon's connector registry.
    NAME = "whatsapp"

    #: WhatsApp's "composing" state auto-expires after ~10-25s, so the typing indicator is
    #: re-sent on this interval until the turn finishes.
    _TYPING_REFRESH_SECONDS = 8.0

    def __init__(
        self,
        session_db: Path,
        dispatch: Dispatch,
        *,
        transcriber: Transcriber | None = None,
        show_active: bool = True,
    ) -> None:
        self._session_db = session_db
        self._dispatch = dispatch  # channel-bound: (sender_id, name, text, send)
        self._transcriber = transcriber
        # Blue-tick + typing presence always travel together — one flag drives both.
        self._show_active = show_active
        self._client: Any = None  # the live client while start() runs (for send_to)

    def build_client(self) -> NewAClient:
        """Create a neonize client wired to the dispatcher.

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
        async def _on_connected(connected: NewAClient, _event: ConnectedEv) -> None:
            logger.info("whatsapp connected")
            # WhatsApp only delivers our read receipts / typing presence to the other party
            # once we've announced ourselves available; send it once on connect.
            if self._show_active:
                try:
                    from neonize.utils import Presence

                    await connected.send_presence(Presence.AVAILABLE)
                except Exception:  # pragma: no cover - presence is best-effort
                    logger.debug("send_presence(available) failed", exc_info=True)

        @client.event(PairStatusEv)  # type: ignore[untyped-decorator]
        async def _on_pair(_client: NewAClient, event: PairStatusEv) -> None:
            logger.info("whatsapp paired as %s", event.ID.User)

        @client.event(MessageEv)  # type: ignore[untyped-decorator]
        async def _on_message(client: NewAClient, message: MessageEv) -> None:
            text = _message_text(message)
            was_voice = False
            if not text:
                text = await self._transcribe_voice(client, message)
                was_voice = bool(text)  # inbound was a (transcribed) voice note
            if text:
                source = message.Info.MessageSource
                chat = source.Chat  # JID to send media replies to
                # Record where this turn came from, so scheduling tools (cron) can
                # capture the chat for later proactive delivery (phone-number JID, not
                # the undeliverable @lid identity).
                current_chat.set((self.NAME, _deliverable_chat(source)))

                async def send(reply: Reply) -> None:
                    # An image reply goes out as a real WhatsApp image; text replies
                    # quote the inbound message as before.
                    if isinstance(reply, Media):
                        await client.send_image(
                            chat, str(reply.path), caption=reply.caption or None
                        )
                    else:
                        await client.reply_message(reply, message)

                # Acknowledge the moment work starts: blue-tick the message and start the
                # "typing…" indicator (best-effort; never blocks or breaks the turn).
                await self._mark_read(client, source, message.Info.ID)
                typing = await self._begin_typing(client, chat, was_voice)
                # Identity is the *sender* (who), not the chat (where to reply); they
                # coincide for DMs, differ in groups. PushName is WhatsApp's display name.
                name = getattr(message.Info, "Pushname", "") or ""
                try:
                    await self._dispatch(_sender_jid(source), name, text, send)
                finally:
                    await self._end_typing(client, chat, typing)

        return client

    async def _mark_read(self, client: Any, source: Any, msg_id: str) -> None:
        """Blue-tick the inbound message (best-effort). Uses the message's own chat/sender.

        Read receipts key off the message's real chat + sender JIDs (not the ``@lid``
        rewrite used when *sending* media), so the raw inbound JIDs are correct here.
        """
        if not self._show_active:
            return
        try:
            from neonize.utils import ReceiptType

            await client.mark_read(
                msg_id, chat=source.Chat, sender=source.Sender, receipt=ReceiptType.READ
            )
        except Exception:  # pragma: no cover - receipts are best-effort
            logger.debug("mark_read failed", exc_info=True)

    async def _begin_typing(
        self, client: Any, chat: Any, was_voice: bool
    ) -> asyncio.Task[None] | None:
        """Show "typing…" (or "recording audio…" for a voice reply) and keep it alive.

        Sends the first ``composing`` synchronously (so the indicator is up before the turn
        runs), then returns a task that re-sends it on :attr:`_TYPING_REFRESH_SECONDS` until
        cancelled by :meth:`_end_typing` — WhatsApp's composing state otherwise expires.
        """
        if not self._show_active:
            return None
        await self._send_presence(client, chat, composing=True, was_voice=was_voice)
        return asyncio.create_task(self._typing_loop(client, chat, was_voice))

    async def _typing_loop(self, client: Any, chat: Any, was_voice: bool) -> None:
        try:
            while True:
                await asyncio.sleep(self._TYPING_REFRESH_SECONDS)
                await self._send_presence(client, chat, composing=True, was_voice=was_voice)
        except asyncio.CancelledError:  # pragma: no cover - cancelled when the turn ends
            pass

    async def _end_typing(self, client: Any, chat: Any, typing: asyncio.Task[None] | None) -> None:
        """Stop the keepalive and clear the indicator (``paused``)."""
        if typing is not None:
            typing.cancel()
            try:
                await typing
            except asyncio.CancelledError:  # pragma: no cover - expected on cancel
                pass
        if self._show_active:
            await self._send_presence(client, chat, composing=False, was_voice=False)

    async def _send_presence(
        self, client: Any, chat: Any, *, composing: bool, was_voice: bool
    ) -> None:
        """Send one chat-presence update (best-effort)."""
        try:
            from neonize.utils import ChatPresence, ChatPresenceMedia

            state = (
                ChatPresence.CHAT_PRESENCE_COMPOSING
                if composing
                else ChatPresence.CHAT_PRESENCE_PAUSED
            )
            media = (
                ChatPresenceMedia.CHAT_PRESENCE_MEDIA_AUDIO
                if was_voice
                else ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT
            )
            await client.send_chat_presence(chat, state, media)
        except Exception:  # pragma: no cover - presence is best-effort
            logger.debug("send_chat_presence failed", exc_info=True)

    async def _transcribe_voice(self, client: Any, message: Any) -> str:
        """Transcript of an inbound voice note, or ``""`` (no audio / no transcriber / error).

        The encrypted audio (ogg/opus) is downloaded to ``~/.gaia/cache/voice/<id>.ogg``
        and transcribed locally; failures are logged and the message dropped — a bad
        voice note must never take the connector loop down.
        """
        if self._transcriber is None:
            return ""
        audio = getattr(message.Message, "audioMessage", None)
        if audio is None or not getattr(audio, "mediaKey", b""):  # no audio payload
            return ""

        path = constants.CACHE_DIR / "voice" / f"{message.Info.ID}.ogg"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            await client.download_any(message.Message, str(path))
            transcript = await self._transcriber.transcribe(path)
        except Exception:
            logger.warning("voice note dropped: download/transcription failed", exc_info=True)
            return ""
        if not transcript:
            logger.info("voice note transcribed to empty text — ignored")
            return ""
        # The prefix tells Gaia the modality, so it answers the spoken content naturally.
        return f"[voice message] {transcript}"

    async def start(self) -> None:
        """Connect (prompting a QR scan on first run) and block receiving events.

        On cancellation (Ctrl-C / ``gaia stop``) the ``finally`` calls :func:`_stop_client`.
        This is load-bearing: ``idle()`` awaits neonize's background ``connect_task``, whose
        blocking ``Neonize()`` Go call runs in a **non-daemon** worker thread. A plain
        ``disconnect()`` only closes the websocket — the worker thread keeps running and the
        interpreter then hangs forever joining it at exit. ``stop()`` (Go-side ``Stop``) is
        what actually unblocks that call so the thread can exit.
        """
        client = self.build_client()
        logger.info("whatsapp starting — scan the QR if prompted (session: %s)", self._session_db)
        await client.connect()
        self._client = client  # expose the live client for proactive send_to
        try:
            await client.idle()  # blocks, keeps receiving events
        finally:
            self._client = None
            await _stop_client(client)

    async def send_to(self, chat: str, reply: Reply) -> None:
        """Proactively send ``reply`` to ``chat`` (``user@server``) — used by cron.

        Only works while :meth:`start` is connected (the daemon); raises otherwise so
        the caller logs a clear delivery failure instead of silently dropping it.
        """
        if self._client is None:
            raise RuntimeError("whatsapp connector is not running")
        from neonize.utils import build_jid

        user, _, server = chat.partition("@")
        jid = build_jid(user, server or "s.whatsapp.net")
        if isinstance(reply, Media):
            await self._client.send_image(jid, str(reply.path), caption=reply.caption or None)
        else:
            await self._client.send_message(jid, reply)


async def _stop_client(client: Any) -> None:
    """Stop a neonize client so its non-daemon worker thread exits (best-effort).

    Prefers ``stop()`` (``Stop`` — unblocks the blocking Go call AND disconnects); falls
    back to ``disconnect()`` for any client that lacks it. Tolerates sync or async methods.
    """
    for name in ("stop", "disconnect"):
        method = getattr(client, name, None)
        if method is None:
            continue
        try:
            result = method()
            if inspect.isawaitable(result):
                await result
            return
        except Exception:  # pragma: no cover - shutdown best-effort
            logger.debug("whatsapp %s failed", name, exc_info=True)
            return
