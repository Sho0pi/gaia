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

    from gaia.config import GroupTrigger
    from gaia.voice import Transcriber

logger = logging.getLogger(__name__)


def _user_part(jid: str) -> str:
    """The user (number) part of a ``user@server`` JID string, or the whole string."""
    return jid.partition("@")[0]


def _context_info(message: Any) -> Any:
    """The inbound message's ``extendedTextMessage.contextInfo`` (or ``None``)."""
    extended = getattr(message.Message, "extendedTextMessage", None)
    return getattr(extended, "contextInfo", None)


def _is_mentioned(message: Any, own_jid: str) -> bool:
    """Whether Gaia's own number appears in the message's ``mentionedJID`` list."""
    if not own_jid:
        return False
    context = _context_info(message)
    mentioned = getattr(context, "mentionedJID", None) or []
    me = _user_part(own_jid)
    return any(_user_part(str(jid)) == me for jid in mentioned)


def _is_reply_to(message: Any, own_jid: str) -> bool:
    """Whether the message replies to (quotes) a message Gaia authored.

    The quoted message's author is carried on ``contextInfo.participant``; when that is
    Gaia's own number, the user replied to Gaia.
    """
    if not own_jid:
        return False
    context = _context_info(message)
    participant = getattr(context, "participant", "") or ""
    return bool(participant) and _user_part(str(participant)) == _user_part(own_jid)


def _group_decision(
    message: Any, source: Any, own_jid: str, cfg: GroupTrigger, allowed: list[str]
) -> bool:
    """Whether Gaia should handle this message given the group policy.

    DMs always pass (the gate is group-only). In a group, Gaia responds only when it is
    *addressed* (mentioned or replied-to, when ``mention_only``) **and** the sender is on
    the connector's ``allow`` list — an empty allow-list means no one.
    """
    if not getattr(source, "IsGroup", False):
        return True
    if not cfg.respond_in_groups:
        return False
    addressed = _is_mentioned(message, own_jid) or _is_reply_to(message, own_jid)
    if cfg.mention_only and not addressed:
        return False
    sender = _sender_jid(source)
    allow = {_user_part(a) for a in allowed}
    return _user_part(sender) in allow


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
    ``group_trigger`` decides when Gaia answers inside a group chat (mention/reply); the
    sender must also be on ``allowed_users`` (the connector's ``allow`` list). ``None`` /
    empty fall back to the default quiet policy.
    """

    #: Connector id used in cron job channel fields / the daemon's connector registry.
    NAME = "whatsapp"

    def __init__(
        self,
        session_db: Path,
        dispatch: Dispatch,
        *,
        transcriber: Transcriber | None = None,
        group_trigger: GroupTrigger | None = None,
        allowed_users: list[str] | None = None,
    ) -> None:
        self._session_db = session_db
        self._dispatch = dispatch  # channel-bound: (sender_id, name, text, send)
        self._transcriber = transcriber
        if group_trigger is None:
            from gaia.config import GroupTrigger

            group_trigger = GroupTrigger()
        self._group_trigger = group_trigger
        self._allowed_users = allowed_users or []  # the connector's `allow` list (group gate)
        self._own_jid = ""  # the bot's own "user@server"; fetched lazily on first need
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
            if not text:
                text = await self._transcribe_voice(client, message)
            if text:
                source = message.Info.MessageSource
                if not await self._should_handle(client, message, source):
                    return  # group message Gaia isn't addressed in / sender not allowed
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

                # Identity is the *sender* (who), not the chat (where to reply); they
                # coincide for DMs, differ in groups. PushName is WhatsApp's display name.
                name = getattr(message.Info, "Pushname", "") or ""
                await self._dispatch(_sender_jid(source), name, text, send)

        return client

    async def _should_handle(self, client: Any, message: Any, source: Any) -> bool:
        """Apply the group policy: drop group messages Gaia isn't addressed in / not allowed.

        DMs always pass. Resolving the group decision needs Gaia's own JID (to match
        mentions/replies), fetched once and cached.
        """
        if not getattr(source, "IsGroup", False):
            return True
        own_jid = await self._ensure_own_jid(client)
        if _group_decision(message, source, own_jid, self._group_trigger, self._allowed_users):
            return True
        logger.debug("group message ignored (not addressed / sender not allowed)")
        return False

    async def _ensure_own_jid(self, client: Any) -> str:
        """Gaia's own ``user@server`` JID (cached); ``""`` if it can't be determined."""
        if not self._own_jid:
            try:
                me = await client.get_me()
                self._own_jid = _jid_to_str(me.JID)
            except Exception:  # pragma: no cover - identity lookup is best-effort
                logger.debug("could not resolve own JID for group mention check", exc_info=True)
        return self._own_jid

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
