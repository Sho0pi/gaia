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
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gaia import constants
from gaia.connectors.base import Dispatch, Inbound, InboundMedia, Media, Reply, current_chat

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


def _is_mentioned(message: Any, own_ids: set[str]) -> bool:
    """Whether one of Gaia's own ids appears in the message's ``mentionedJID`` list.

    WhatsApp may carry the bot's mention as its phone JID *or* its ``@lid`` identity, so
    ``own_ids`` holds both number-parts and we match against either.
    """
    if not own_ids:
        return False
    context = _context_info(message)
    mentioned = getattr(context, "mentionedJID", None) or []
    return any(_user_part(str(jid)) in own_ids for jid in mentioned)


def _is_reply_to(message: Any, own_ids: set[str]) -> bool:
    """Whether the message replies to (quotes) a message Gaia authored.

    The quoted message's author is carried on ``contextInfo.participant``; when that is one
    of Gaia's own ids (phone or ``@lid``), the user replied to Gaia.
    """
    if not own_ids:
        return False
    context = _context_info(message)
    participant = getattr(context, "participant", "") or ""
    return bool(participant) and _user_part(str(participant)) in own_ids


def _group_decision(message: Any, source: Any, own_ids: set[str], cfg: GroupTrigger) -> bool:
    """Whether Gaia should *consider* this group message (was it addressed to Gaia?).

    DMs always pass (the gate is group-only). In a group Gaia engages only when it is
    *addressed* — @mentioned or someone replies to one of its messages — when ``mention_only``
    is set. **Who** is allowed to trigger Gaia is **not** decided here: that is the user/role
    system (``users.json`` + the dispatcher's guest-drop), so we don't duplicate an allow-list.
    """
    if not getattr(source, "IsGroup", False):
        return True
    if not cfg.respond_in_groups:
        return False
    addressed = _is_mentioned(message, own_ids) or _is_reply_to(message, own_ids)
    return addressed or not cfg.mention_only


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


#: Downloadable inbound attachments, as ``(neonize proto field, InboundMedia.kind)``. The
#: handler hands each to the model as a file part, so the kind only labels it; stickers are
#: webp images. Audio is *not* here — it goes through the transcription path instead.
_MEDIA_KINDS: tuple[tuple[str, str], ...] = (
    ("imageMessage", "image"),
    ("videoMessage", "video"),
    ("documentMessage", "document"),
    ("stickerMessage", "image"),
)

#: Fallback mime when a proto omits ``mimetype``, by kind.
_DEFAULT_MIME = {
    "image": "image/jpeg",
    "video": "video/mp4",
    "document": "application/octet-stream",
}


def _vcard_phone(vcard: str) -> str:
    """The first phone number in a vCard's ``TEL`` line, as `` <number>`` (or ``""``)."""
    match = re.search(r"TEL[^:\n]*:\s*([+\d][\d\s().-]+)", vcard or "")
    return f" {match.group(1).strip()}" if match else ""


def _describe_special(message: Any) -> str:
    """A text proxy for inbound types that are neither text nor a downloadable file.

    Location and shared contacts have no file to hand the model, so we turn them into a short
    bracketed line Gaia answers like any text. Returns ``""`` when none apply (so the message is
    ignored as before). Interactive/poll types are a follow-up.
    """
    msg = message.Message
    loc = getattr(msg, "locationMessage", None) or getattr(msg, "liveLocationMessage", None)
    if loc is not None:
        lat = getattr(loc, "degreesLatitude", None)
        lng = getattr(loc, "degreesLongitude", None)
        if lat or lng:
            label = getattr(loc, "name", None) or getattr(loc, "address", None) or ""
            where = f" ({label})" if label else ""
            return f"[Location{where}: {lat},{lng} — https://maps.google.com/?q={lat},{lng}]"
    contact = getattr(msg, "contactMessage", None)
    name = getattr(contact, "displayName", None) if contact is not None else None
    if name:
        return f"[Contact: {name}{_vcard_phone(getattr(contact, 'vcard', None) or '')}]"
    array = getattr(msg, "contactsArrayMessage", None)
    contacts = getattr(array, "contacts", None) if array is not None else None
    if contacts:
        names = ", ".join(
            f"{getattr(c, 'displayName', None) or ''}"
            f"{_vcard_phone(getattr(c, 'vcard', None) or '')}".strip()
            for c in contacts
        )
        return f"[Contacts: {names}]"
    return ""


def _media_message(message: Any) -> tuple[Any, str] | None:
    """The first real downloadable attachment as ``(proto, kind)``, else ``None``.

    Like the voice check, the proto field is always present, so a non-empty ``mediaKey`` is
    what distinguishes a real attachment from the default-empty one.
    """
    msg = message.Message
    for field, kind in _MEDIA_KINDS:
        proto = getattr(msg, field, None)
        if proto is not None and getattr(proto, "mediaKey", None):
            return proto, kind
    return None


class WhatsAppWebConnector:
    """Bridges regular-account WhatsApp messages to the dispatcher (per-sender identity).

    ``transcriber`` (a :class:`gaia.voice.Transcriber`) turns inbound voice notes into
    text for the handler; ``None`` means voice messages are ignored (prior behaviour).
    ``group_trigger`` decides when Gaia answers inside a group chat (mention/reply); *who*
    may trigger it is left to the user/role system (``users.json`` + the dispatcher's
    guest-drop), not a separate allow-list. ``None`` falls back to the default quiet policy.
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
        group_trigger: GroupTrigger | None = None,
        show_active: bool = True,
    ) -> None:
        self._session_db = session_db
        self._dispatch = dispatch  # channel-bound: (sender_id, name, text, send)
        self._transcriber = transcriber
        if group_trigger is None:
            from gaia.config import GroupTrigger

            group_trigger = GroupTrigger()
        self._group_trigger = group_trigger
        self._own_ids: set[str] = set()  # bot's own number-parts (phone + @lid); lazy-loaded
        # Blue-tick + typing presence always travel together — one flag drives both.
        self._show_active = show_active
        self._client: Any = None  # the live client while start() runs (for send_to)
        # Set by ConnectedEv/PairStatusEv; pair() awaits it. Re-created per client.
        self._connected = asyncio.Event()

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
        self._connected = asyncio.Event()
        client = NewAClient(str(self._session_db))

        @client.event(ConnectedEv)  # type: ignore[untyped-decorator]
        async def _on_connected(connected: NewAClient, _event: ConnectedEv) -> None:
            logger.info("whatsapp connected")
            self._connected.set()
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
            self._connected.set()

        @client.event(MessageEv)  # type: ignore[untyped-decorator]
        async def _on_message(client: NewAClient, message: MessageEv) -> None:
            source = message.Info.MessageSource
            # Entry trace: confirms an inbound event arrived at all (esp. for groups) before
            # any text/gate logic — invaluable when "nothing happens" in a group.
            logger.debug(
                "inbound whatsapp message: group=%s chat=%s sender=%s",
                getattr(source, "IsGroup", False),
                _deliverable_chat(source),
                _sender_jid(source),
            )
            text = _message_text(message)
            was_voice = False
            media: tuple[InboundMedia, ...] = ()
            found = _media_message(message)  # image/video/document/sticker, with optional caption
            if found is not None:
                proto, kind = found
                item = await self._download_media(client, message, proto, kind)
                if item is not None:
                    media = (item,)
                    text = text or (getattr(proto, "caption", "") or "")
            elif not text:
                voice = await self._transcribe_voice(client, message)
                if voice:
                    text, was_voice = voice, True  # a (transcribed) voice note
                else:
                    text = _describe_special(message)  # location/contact → a text proxy
            if text or media:
                if not await self._should_handle(client, message, source):
                    return  # a group message Gaia wasn't addressed in (mention/reply)
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
                    await self._dispatch(
                        _sender_jid(source), name, Inbound(text=text, media=media), send
                    )
                finally:
                    await self._end_typing(client, chat, typing)

        return client

    async def _should_handle(self, client: Any, message: Any, source: Any) -> bool:
        """Apply the group policy: drop group messages Gaia isn't addressed in / not allowed.

        DMs always pass. Resolving the group decision needs Gaia's own JID (to match
        mentions/replies), fetched once and cached.
        """
        if not getattr(source, "IsGroup", False):
            return True
        own_ids = await self._ensure_own_ids(client)
        if _group_decision(message, source, own_ids, self._group_trigger):
            return True
        context = _context_info(message)
        logger.debug(
            "group message ignored — not addressed. own_ids=%s mentioned=%s reply_author=%s",
            sorted(own_ids),
            list(getattr(context, "mentionedJID", None) or []),
            getattr(context, "participant", ""),
        )
        return False

    async def _ensure_own_ids(self, client: Any) -> set[str]:
        """Gaia's own number-parts — phone JID **and** ``@lid`` — cached.

        WhatsApp may address the bot in a group by either identity, so a mention only
        matches if we know both. ``get_me()`` returns a Device carrying ``JID`` (phone) and
        ``LID``. Empty/unset ids are skipped; an empty set means we couldn't resolve them.
        """
        if not self._own_ids:
            try:
                me = await client.get_me()
                for jid in (getattr(me, "JID", None), getattr(me, "LID", None)):
                    part = _user_part(_jid_to_str(jid)) if jid is not None else ""
                    if part:
                        self._own_ids.add(part)
            except Exception:  # pragma: no cover - identity lookup is best-effort
                logger.debug("could not resolve own ids for group mention check", exc_info=True)
        return self._own_ids

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

    async def _download_media(
        self, client: Any, message: Any, proto: Any, kind: str
    ) -> InboundMedia | None:
        """Download an inbound attachment (image/video/document/sticker) to ``UPLOADS_DIR``.

        WhatsApp media is E2E-encrypted on its servers, so it must be downloaded (not read
        inline). Saved under UPLOADS_DIR (a sandbox root) so a soul can copy it into its
        workspace and use the real file. Best-effort like the voice path — a bad attachment
        must never take the connector loop down; returns ``None`` on failure.
        """
        import mimetypes

        mime = getattr(proto, "mimetype", "") or _DEFAULT_MIME[kind]
        # Prefer a document's own extension; else derive one from the mime.
        ext = (
            Path(getattr(proto, "fileName", "") or "").suffix
            or mimetypes.guess_extension(mime.split(";")[0])
            or ".bin"
        )
        path = constants.UPLOADS_DIR / f"{message.Info.ID}{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            await client.download_any(message.Message, str(path))
        except Exception:
            logger.warning("inbound %s dropped: download failed", kind, exc_info=True)
            return None
        return InboundMedia(path=path, mime=mime, kind=kind)

    async def pair(self, timeout_s: float = 120.0) -> bool:
        """Foreground QR pairing: connect, wait for scan, then shut the client down.

        ``connect()`` makes neonize render the QR in the terminal. The ConnectedEv /
        PairStatusEv handlers set ``_connected``. Returns True once paired (the session
        persists to the db so later runs reconnect without a scan), False on timeout.
        """
        client = self.build_client()
        try:
            await client.connect()
            await asyncio.wait_for(self._connected.wait(), timeout=timeout_s)
            return True
        except TimeoutError:
            return False
        finally:
            await _stop_client(client)

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
