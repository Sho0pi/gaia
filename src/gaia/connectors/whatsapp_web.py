"""Regular-account WhatsApp adapter built on neonize (whatsmeow bindings).

Unlike the Cloud-API :class:`~gaia.connectors.whatsapp.WhatsAppConnector`, this
talks the WhatsApp-Web multidevice protocol: a personal account pairs by scanning
a QR code printed to the terminal on first run, and the session is persisted to a
local SQLite db so subsequent runs reconnect without a new scan.

Deferred import keeps the module importable without the neonize dep installed, so
unit tests can exercise the wiring without the native whatsmeow binary.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gaia import constants
from gaia.connectors.base import Dispatch, Media, Reply, current_chat

if TYPE_CHECKING:  # pragma: no cover - typing only
    from neonize.aioze.client import NewAClient

    from gaia.voice import Synthesizer, Transcriber

logger = logging.getLogger(__name__)


def _build_jid(chat: str) -> Any:
    """A neonize JID from a ``user@server`` string (default server ``s.whatsapp.net``).

    The phone-number address media sends must target — sending to a ``…@lid`` identity
    drops silently. Shared by the inbound media reply path and proactive ``send_to``.
    """
    from neonize.utils import build_jid

    user, _, server = chat.partition("@")
    return build_jid(user, server or "s.whatsapp.net")


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
    ``synthesizer`` (a :class:`gaia.voice.Synthesizer`) speaks text replies back as a voice
    note — but only when the inbound message was itself voice (voice-in → voice-out).
    """

    #: Connector id used in cron job channel fields / the daemon's connector registry.
    NAME = "whatsapp"

    def __init__(
        self,
        session_db: Path,
        dispatch: Dispatch,
        *,
        transcriber: Transcriber | None = None,
        synthesizer: Synthesizer | None = None,
    ) -> None:
        self._session_db = session_db
        self._dispatch = dispatch  # channel-bound: (sender_id, name, text, send)
        self._transcriber = transcriber
        self._synthesizer = synthesizer
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
        async def _on_connected(_client: NewAClient, _event: ConnectedEv) -> None:
            logger.info("whatsapp connected")

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
                # Media (image / voice note) must go to the *deliverable* JID, not the raw
                # ``source.Chat``: in a DM that Chat is a ``…@lid`` identity and a media send
                # to it vanishes silently (the user gets nothing — not even the text). Build
                # the phone-number JID the same way proactive ``send_to`` does.
                chat = _build_jid(_deliverable_chat(source))
                # Record where this turn came from, so scheduling tools (cron) can
                # capture the chat for later proactive delivery (phone-number JID, not
                # the undeliverable @lid identity).
                current_chat.set((self.NAME, _deliverable_chat(source)))

                async def send(reply: Reply) -> None:
                    # An image reply goes out as a real WhatsApp image. A text reply to a
                    # *voice* message is spoken back as a voice note (voice-in → voice-out);
                    # otherwise it quotes the inbound message as before.
                    if isinstance(reply, Media):
                        await client.send_image(
                            chat, str(reply.path), caption=reply.caption or None
                        )
                        return
                    if was_voice and await self._speak(client, chat, reply):
                        return
                    await client.reply_message(reply, message)

                # Identity is the *sender* (who), not the chat (where to reply); they
                # coincide for DMs, differ in groups. PushName is WhatsApp's display name.
                name = getattr(message.Info, "Pushname", "") or ""
                await self._dispatch(_sender_jid(source), name, text, send)

        return client

    async def _speak(self, client: Any, chat: Any, text: str) -> bool:
        """Speak ``text`` as a voice note (PTT); True if sent, False to fall back to text.

        Empty/whitespace replies and synthesis failures fall back so the user still gets the
        answer; a bad TTS must never swallow the reply or take the loop down.
        """
        if self._synthesizer is None or not text.strip():
            return False
        try:
            ogg = await self._synthesizer.synthesize(text)
            if ogg is None:
                return False
            # neonize's send_audio stamps the mimetype from a libmagic sniff, which yields a
            # bare 'audio/ogg'. WhatsApp only renders a PTT voice note when the mimetype is
            # 'audio/ogg; codecs=opus' — with the bare type it accepts the upload but the
            # recipient gets nothing. Build the message, then correct that one field.
            msg = await client.build_audio_message(str(ogg), ptt=True)
            msg.audioMessage.mimetype = "audio/ogg; codecs=opus"
            await client.send_message(chat, msg)
            return True
        except Exception:  # pragma: no cover - never lose the reply to a bad voice send
            logger.warning("voice reply failed; falling back to text", exc_info=True)
            return False

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
        # Hand the spoken words over as a normal request. A *leading* "[voice message]" tag
        # made the model treat it as casual chat and answer in one shot without ever calling
        # tools (verified: voice turns skipped web_search etc. that the same typed question
        # triggered). Put the query first and the modality as a trailing instruction that
        # explicitly preserves tool use, only asking for a concise (spoken-friendly) answer.
        return (
            f"{transcript}\n\n"
            "(This arrived as a voice message — handle it exactly like a typed request, "
            "using your tools whenever they help; just keep the reply concise since it will "
            "be read aloud.)"
        )

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
        jid = _build_jid(chat)
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
