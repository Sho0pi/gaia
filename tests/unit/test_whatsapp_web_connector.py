"""Unit tests for the neonize-backed regular-account WhatsApp connector.

neonize ships a native whatsmeow binary we don't want in unit tests, so the
``neonize.aioze`` modules are faked in ``sys.modules`` and the connector's lazy
import picks them up. This verifies the wiring (handler bridge, text extraction,
reply) without touching the real library.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from gaia.connectors.base import Inbound, Send
from gaia.connectors.whatsapp_web import WhatsAppWebConnector, _message_text


def _msg(
    *,
    conversation: str = "",
    extended: str = "",
    quoted: str = "",
    mentioned: tuple[str, ...] = (),
    reply_author: str = "",
) -> SimpleNamespace:
    """Build a fake neonize MessageEv with the fields the connector reads.

    ``quoted`` populates extendedTextMessage.contextInfo.quotedMessage (a reply);
    ``mentioned`` populates ``mentionedJID`` and ``reply_author`` the quoted message's
    author (``participant``) — both used by the group-mention gate.
    """
    quoted_message = (
        SimpleNamespace(conversation=quoted, extendedTextMessage=SimpleNamespace(text=""))
        if quoted
        else None
    )
    context_info = SimpleNamespace(
        quotedMessage=quoted_message,
        mentionedJID=list(mentioned),
        participant=reply_author,
    )
    return SimpleNamespace(
        Message=SimpleNamespace(
            conversation=conversation,
            extendedTextMessage=SimpleNamespace(text=extended, contextInfo=context_info),
        ),
        Info=SimpleNamespace(
            ID="MSGID",
            MessageSource=SimpleNamespace(Chat="chat-jid", Sender="sender-jid"),
        ),
    )


def _group_source(sender: str = "555@s.whatsapp.net") -> SimpleNamespace:
    """A group MessageSource: shared @g.us chat + an individual sender."""
    user, _, server = sender.partition("@")
    return SimpleNamespace(
        Chat=SimpleNamespace(User="group", Server="g.us"),
        IsGroup=True,
        Sender=SimpleNamespace(User=user, Server=server or "s.whatsapp.net"),
    )


class _FakeClient:
    def __init__(self, name: str) -> None:
        self.name = name
        self.handlers: dict[Any, Any] = {}
        self.replies: list[tuple[str, Any]] = []
        self.images: list[tuple[Any, str, str | None]] = []
        self.media_sends: list[tuple[str, Any, str, str | None]] = []
        self.doc_filenames: list[str | None] = []
        self.connected = False
        self.stopped = False
        self.reads: list[tuple[str, Any, Any, Any]] = []
        self.presence: list[tuple[Any, Any, Any]] = []  # (chat, state, media)
        self.availability: list[Any] = []

    async def connect(self) -> None:
        self.connected = True

    async def idle(self) -> None:
        await asyncio.Event().wait()  # blocks until the task is cancelled

    async def stop(self) -> None:  # the method that unblocks neonize's Go worker thread
        self.stopped = True

    def event(self, event_type: Any) -> Any:
        def register(fn: Any) -> Any:
            self.handlers[event_type] = fn
            return fn

        return register

    async def reply_message(self, text: str, message: Any) -> None:
        self.replies.append((text, message))

    async def send_image(self, to: Any, file: str, caption: str | None = None) -> None:
        self.images.append((to, file, caption))
        self.media_sends.append(("send_image", to, file, caption))

    async def send_video(self, to: Any, file: str, caption: str | None = None) -> None:
        self.media_sends.append(("send_video", to, file, caption))

    async def send_audio(self, to: Any, file: str, caption: str | None = None) -> None:
        self.media_sends.append(("send_audio", to, file, caption))

    async def send_document(
        self, to: Any, file: str, caption: str | None = None, filename: str | None = None
    ) -> None:
        self.media_sends.append(("send_document", to, file, caption))
        self.doc_filenames.append(filename)

    async def get_me(self) -> SimpleNamespace:
        # Device carries both the phone JID and the @lid identity.
        return SimpleNamespace(
            JID=SimpleNamespace(User="bot", Server="s.whatsapp.net"),
            LID=SimpleNamespace(User="lidbot", Server="lid"),
        )

    async def mark_read(self, *ids: str, chat: Any, sender: Any, receipt: Any) -> None:
        self.reads.append((ids[0], chat, sender, receipt))

    async def send_chat_presence(self, chat: Any, state: Any, media: Any) -> None:
        self.presence.append((chat, state, media))

    async def send_presence(self, presence: Any) -> None:
        self.availability.append(presence)


@pytest.fixture
def fake_neonize(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Install fake neonize.aioze.client / .events modules; return the event classes."""
    connected_ev, message_ev, pair_status_ev = (type(n, (), {}) for n in ("C", "M", "P"))

    client_mod = ModuleType("neonize.aioze.client")
    client_mod.NewAClient = _FakeClient  # type: ignore[attr-defined]
    events_mod = ModuleType("neonize.aioze.events")
    events_mod.ConnectedEv = connected_ev  # type: ignore[attr-defined]
    events_mod.MessageEv = message_ev  # type: ignore[attr-defined]
    events_mod.PairStatusEv = pair_status_ev  # type: ignore[attr-defined]

    # neonize.utils: the presence/receipt enums the connector imports lazily.
    utils_mod = ModuleType("neonize.utils")
    utils_mod.ReceiptType = SimpleNamespace(READ="read")  # type: ignore[attr-defined]
    utils_mod.ChatPresence = SimpleNamespace(  # type: ignore[attr-defined]
        CHAT_PRESENCE_COMPOSING="composing", CHAT_PRESENCE_PAUSED="paused"
    )
    utils_mod.ChatPresenceMedia = SimpleNamespace(  # type: ignore[attr-defined]
        CHAT_PRESENCE_MEDIA_TEXT="text", CHAT_PRESENCE_MEDIA_AUDIO="audio"
    )
    utils_mod.Presence = SimpleNamespace(AVAILABLE="available")  # type: ignore[attr-defined]

    for name, mod in {
        "neonize": ModuleType("neonize"),
        "neonize.aioze": ModuleType("neonize.aioze"),
        "neonize.aioze.client": client_mod,
        "neonize.aioze.events": events_mod,
        "neonize.utils": utils_mod,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)

    return {"MessageEv": message_ev, "ConnectedEv": connected_ev}


@pytest.mark.parametrize(
    ("conversation", "extended", "expected"),
    [("hi", "", "hi"), ("", "from-extended", "from-extended")],
)
def test_message_text_extraction(conversation: str, extended: str, expected: str) -> None:
    assert _message_text(_msg(conversation=conversation, extended=extended)) == expected


def test_quoted_reply_includes_the_quoted_message() -> None:
    # Replying to an earlier message must surface that message so Gaia has the context.
    text = _message_text(_msg(extended="what about this one?", quoted="the original question"))

    assert "the original question" in text
    assert "what about this one?" in text


def test_no_quote_returns_plain_text() -> None:
    assert _message_text(_msg(conversation="just a normal message")) == "just a normal message"


async def _noop_dispatch(_sender_id: str, _name: str, _inbound: Inbound, _send: Send) -> None:
    return None


def test_build_client_creates_session_dir(fake_neonize: dict[str, Any], tmp_path: Path) -> None:
    db = tmp_path / "nested" / "whatsapp.db"

    WhatsAppWebConnector(db, _noop_dispatch).build_client()

    assert db.parent.is_dir()


async def test_inbound_message_routed_to_dispatch(
    fake_neonize: dict[str, Any], tmp_path: Path
) -> None:
    seen: list[str] = []

    async def dispatch(_sender_id: str, _name: str, inbound: Inbound, send: Send) -> None:
        seen.append(inbound.text)
        # Stream two replies to prove the sink can fan out one inbound to many.
        await send(f"echo:{inbound.text}")
        await send("again")

    client = WhatsAppWebConnector(tmp_path / "wa.db", dispatch).build_client()
    message = _msg(conversation="ping")

    await client.handlers[fake_neonize["MessageEv"]](client, message)

    assert seen == ["ping"]
    assert client.replies == [("echo:ping", message), ("again", message)]


async def test_media_reply_sent_as_image(fake_neonize: dict[str, Any], tmp_path: Path) -> None:
    from gaia.connectors.base import Media

    async def dispatch(_sender_id: str, _name: str, _inbound: Inbound, send: Send) -> None:
        await send("here:")  # a text reply, then the image
        await send(Media(Path("/tmp/shot.png"), caption="screenshot"))

    client = WhatsAppWebConnector(tmp_path / "wa.db", dispatch).build_client()
    await client.handlers[fake_neonize["MessageEv"]](client, _msg(conversation="shot"))

    assert [text for text, _ in client.replies] == ["here:"]  # text reply still sent
    assert client.images == [("chat-jid", "/tmp/shot.png", "screenshot")]  # image via send_image


@pytest.mark.parametrize(
    ("name", "method"),
    [
        ("clip.mp4", "send_video"),
        ("song.mp3", "send_audio"),
        ("report.pdf", "send_document"),
        ("data.csv", "send_document"),  # unknown-ish type → document
    ],
)
async def test_media_reply_sent_by_kind(
    name: str, method: str, fake_neonize: dict[str, Any], tmp_path: Path
) -> None:
    from gaia.connectors.base import Media

    async def dispatch(_sender_id: str, _name: str, _inbound: Inbound, send: Send) -> None:
        await send(Media(Path("/tmp") / name, caption="here"))

    client = WhatsAppWebConnector(tmp_path / "wa.db", dispatch).build_client()
    await client.handlers[fake_neonize["MessageEv"]](client, _msg(conversation="x"))

    assert client.media_sends == [(method, "chat-jid", f"/tmp/{name}", "here")]
    if method == "send_document":
        assert client.doc_filenames == [name]  # filename set, else WhatsApp shows "Untitled"


async def test_empty_message_is_ignored(fake_neonize: dict[str, Any], tmp_path: Path) -> None:
    async def dispatch(*_a: object) -> None:  # pragma: no cover - must not run
        raise AssertionError("dispatch called on empty message")

    client = WhatsAppWebConnector(tmp_path / "wa.db", dispatch).build_client()

    await client.handlers[fake_neonize["MessageEv"]](client, _msg())

    assert client.replies == []


async def test_lid_chat_captures_phone_number_jid(
    fake_neonize: dict[str, Any], tmp_path: Path
) -> None:
    # LID addressing: Chat is a hidden @lid identity (sends to it vanish); the delivery
    # capture and the sender identity must both prefer SenderAlt — the phone-number JID.
    from gaia.connectors.base import current_chat

    seen: list[str] = []

    async def dispatch(sender_id: str, _name: str, _inbound: Inbound, _send: Send) -> None:
        seen.append(sender_id)

    client = WhatsAppWebConnector(tmp_path / "wa.db", dispatch).build_client()
    msg = _msg(conversation="hi")
    msg.Info.MessageSource = SimpleNamespace(
        Chat=SimpleNamespace(User="160168088236120", Server="lid"),
        IsGroup=False,
        SenderAlt=SimpleNamespace(User="972501234567", Server="s.whatsapp.net"),
    )

    await client.handlers[fake_neonize["MessageEv"]](client, msg)

    assert seen == ["972501234567@s.whatsapp.net"]  # identity = the sender's phone JID
    assert current_chat.get() == ("whatsapp", "972501234567@s.whatsapp.net")  # delivery target


async def test_group_chat_keeps_group_jid(fake_neonize: dict[str, Any], tmp_path: Path) -> None:
    from gaia.config import GroupTrigger
    from gaia.connectors.base import current_chat

    # A group message that passes the gate (open policy here) still captures the group JID.
    connector = WhatsAppWebConnector(
        tmp_path / "wa.db",
        _noop_dispatch,
        group_trigger=GroupTrigger(mention_only=False),
    )
    client = connector.build_client()
    msg = _msg(conversation="hi")
    msg.Info.MessageSource = SimpleNamespace(
        Chat=SimpleNamespace(User="12036302byte", Server="g.us"),
        IsGroup=True,
        Sender=SimpleNamespace(User="111", Server="s.whatsapp.net"),
    )

    await client.handlers[fake_neonize["MessageEv"]](client, msg)

    assert current_chat.get() == ("whatsapp", "12036302byte@g.us")


# --- voice notes ---------------------------------------------------------------------


def _voice_msg() -> SimpleNamespace:
    """A fake MessageEv carrying only an audioMessage (no text)."""
    msg = _msg()  # empty text fields
    msg.Message.audioMessage = SimpleNamespace(mediaKey=b"key", seconds=3)
    msg.Info.ID = "VOICE123"
    return msg


class _FakeTranscriber:
    def __init__(self, text: str = "what is the weather") -> None:
        self.text = text
        self.paths: list[Path] = []

    async def transcribe(self, path: Path) -> str:
        self.paths.append(path)
        return self.text


class _DownloadClient(_FakeClient):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.downloads: list[str] = []

    async def download_any(self, message: Any, path: str) -> None:
        self.downloads.append(path)
        Path(path).write_bytes(b"OGGDATA")  # noqa: ASYNC240 - tiny fixture write in a fake


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "cache"
    monkeypatch.setattr("gaia.constants.CACHE_DIR", cache)
    return cache


async def test_voice_note_transcribed_to_handler(
    fake_neonize: dict[str, Any], tmp_path: Path, cache_dir: Path
) -> None:
    sys.modules["neonize.aioze.client"].NewAClient = _DownloadClient  # type: ignore[attr-defined]
    seen: list[str] = []

    async def dispatch(_sender_id: str, _name: str, inbound: Inbound, _send: Send) -> None:
        seen.append(inbound.text)

    transcriber = _FakeTranscriber()
    connector = WhatsAppWebConnector(tmp_path / "wa.db", dispatch, transcriber=transcriber)
    client = connector.build_client()

    await client.handlers[fake_neonize["MessageEv"]](client, _voice_msg())

    assert seen == ["[voice message] what is the weather"]
    saved = cache_dir / "voice" / "VOICE123.ogg"
    assert saved.read_bytes() == b"OGGDATA"  # audio cached under ~/.gaia/cache/voice/
    assert transcriber.paths == [saved]


async def test_voice_note_ignored_without_transcriber(
    fake_neonize: dict[str, Any], tmp_path: Path, cache_dir: Path
) -> None:
    async def dispatch(*_a: object) -> None:  # pragma: no cover - must not run
        raise AssertionError("voice note must be dropped without a transcriber")

    client = WhatsAppWebConnector(tmp_path / "wa.db", dispatch).build_client()

    await client.handlers[fake_neonize["MessageEv"]](client, _voice_msg())  # no crash


async def test_voice_download_failure_drops_message(
    fake_neonize: dict[str, Any], tmp_path: Path, cache_dir: Path
) -> None:
    class _FailingClient(_FakeClient):
        async def download_any(self, message: Any, path: str) -> None:
            raise RuntimeError("media gone")

    sys.modules["neonize.aioze.client"].NewAClient = _FailingClient  # type: ignore[attr-defined]

    async def dispatch(*_a: object) -> None:  # pragma: no cover - must not run
        raise AssertionError

    connector = WhatsAppWebConnector(tmp_path / "wa.db", dispatch, transcriber=_FakeTranscriber())
    client = connector.build_client()

    await client.handlers[fake_neonize["MessageEv"]](client, _voice_msg())  # logged, no crash


async def test_text_message_pipeline_unchanged_with_transcriber(
    fake_neonize: dict[str, Any], tmp_path: Path, cache_dir: Path
) -> None:
    seen: list[str] = []

    async def dispatch(_sender_id: str, _name: str, inbound: Inbound, _send: Send) -> None:
        seen.append(inbound.text)

    connector = WhatsAppWebConnector(tmp_path / "wa.db", dispatch, transcriber=_FakeTranscriber())
    client = connector.build_client()

    await client.handlers[fake_neonize["MessageEv"]](client, _msg(conversation="plain text"))

    assert seen == ["plain text"]  # no [voice message] prefix, no transcription


# --- inbound images (#6 / #138) ------------------------------------------------------


def _image_msg(caption: str = "") -> SimpleNamespace:
    """A fake MessageEv carrying an imageMessage (with optional caption)."""
    msg = _msg()  # empty text fields
    msg.Message.imageMessage = SimpleNamespace(
        mediaKey=b"key", caption=caption, mimetype="image/jpeg"
    )
    msg.Info.ID = "IMG123"
    return msg


async def test_inbound_image_downloaded_and_dispatched_with_caption(
    fake_neonize: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploads = tmp_path / "uploads"
    monkeypatch.setattr("gaia.constants.UPLOADS_DIR", uploads)
    sys.modules["neonize.aioze.client"].NewAClient = _DownloadClient  # type: ignore[attr-defined]
    captured: list[Inbound] = []

    async def dispatch(_sender_id: str, _name: str, inbound: Inbound, _send: Send) -> None:
        captured.append(inbound)

    client = WhatsAppWebConnector(tmp_path / "wa.db", dispatch).build_client()
    await client.handlers[fake_neonize["MessageEv"]](client, _image_msg(caption="what's this?"))

    assert len(captured) == 1
    inbound = captured[0]
    assert inbound.text == "what's this?"  # caption becomes the turn's text
    assert len(inbound.media) == 1
    item = inbound.media[0]
    assert item.kind == "image" and item.mime == "image/jpeg"
    saved = next(uploads.glob("IMG123.*"))  # downloaded into the sandbox-reachable uploads dir
    assert saved.read_bytes() == b"OGGDATA" and item.path == saved


@pytest.mark.parametrize(
    ("field", "kind", "mime", "extra"),
    [
        ("videoMessage", "video", "video/mp4", {"caption": "watch this"}),
        ("documentMessage", "document", "application/pdf", {"fileName": "report.pdf"}),
        ("stickerMessage", "image", "image/webp", {}),
    ],
)
async def test_inbound_media_types_downloaded_and_dispatched(
    field: str,
    kind: str,
    mime: str,
    extra: dict[str, Any],
    fake_neonize: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Video / document / sticker each download to the sandbox uploads dir and reach the handler
    # as an InboundMedia with the right kind/mime (the handler then hands it to the model).
    uploads = tmp_path / "uploads"
    monkeypatch.setattr("gaia.constants.UPLOADS_DIR", uploads)
    sys.modules["neonize.aioze.client"].NewAClient = _DownloadClient  # type: ignore[attr-defined]
    captured: list[Inbound] = []

    async def dispatch(_sender_id: str, _name: str, inbound: Inbound, _send: Send) -> None:
        captured.append(inbound)

    msg = _msg()
    setattr(msg.Message, field, SimpleNamespace(mediaKey=b"key", mimetype=mime, **extra))
    msg.Info.ID = "MEDIA1"

    client = WhatsAppWebConnector(tmp_path / "wa.db", dispatch).build_client()
    await client.handlers[fake_neonize["MessageEv"]](client, msg)

    assert len(captured) == 1
    item = captured[0].media[0]
    assert item.kind == kind and item.mime == mime
    assert item.path.exists() and item.path.read_bytes() == b"OGGDATA"
    if extra.get("caption"):
        assert captured[0].text == extra["caption"]
    if extra.get("fileName"):
        assert item.path.suffix == ".pdf"  # document keeps its own extension


async def test_inbound_location_becomes_text(fake_neonize: dict[str, Any], tmp_path: Path) -> None:
    captured: list[Inbound] = []

    async def dispatch(_sender_id: str, _name: str, inbound: Inbound, _send: Send) -> None:
        captured.append(inbound)

    msg = _msg()
    msg.Message.locationMessage = SimpleNamespace(
        degreesLatitude=32.07, degreesLongitude=34.78, name="Tel Aviv", address=""
    )
    client = WhatsAppWebConnector(tmp_path / "wa.db", dispatch).build_client()
    await client.handlers[fake_neonize["MessageEv"]](client, msg)

    assert len(captured) == 1 and not captured[0].media
    text = captured[0].text
    assert "32.07,34.78" in text and "Tel Aviv" in text and "maps.google.com" in text


async def test_inbound_contact_becomes_text(fake_neonize: dict[str, Any], tmp_path: Path) -> None:
    captured: list[Inbound] = []

    async def dispatch(_sender_id: str, _name: str, inbound: Inbound, _send: Send) -> None:
        captured.append(inbound)

    msg = _msg()
    msg.Message.contactMessage = SimpleNamespace(
        displayName="Dana", vcard="BEGIN:VCARD\nTEL;waid=972:+972 50-123-4567\nEND:VCARD"
    )
    client = WhatsAppWebConnector(tmp_path / "wa.db", dispatch).build_client()
    await client.handlers[fake_neonize["MessageEv"]](client, msg)

    assert len(captured) == 1
    assert "Dana" in captured[0].text and "+972 50-123-4567" in captured[0].text


@pytest.mark.parametrize(
    ("field", "value", "expect"),
    [
        ("buttonsResponseMessage", SimpleNamespace(selectedDisplayText="Yes"), "[Selected: Yes]"),
        (
            "templateButtonReplyMessage",
            SimpleNamespace(selectedDisplayText="Option B"),
            "[Selected: Option B]",
        ),
        ("listResponseMessage", SimpleNamespace(title="Large"), "[Selected: Large]"),
        ("reactionMessage", SimpleNamespace(text="👍"), "[Reacted 👍 to a message]"),
    ],
)
async def test_inbound_interactive_replies_become_text(
    field: str, value: Any, expect: str, fake_neonize: dict[str, Any], tmp_path: Path
) -> None:
    captured: list[Inbound] = []

    async def dispatch(_sender_id: str, _name: str, inbound: Inbound, _send: Send) -> None:
        captured.append(inbound)

    msg = _msg()
    setattr(msg.Message, field, value)
    client = WhatsAppWebConnector(tmp_path / "wa.db", dispatch).build_client()
    await client.handlers[fake_neonize["MessageEv"]](client, msg)

    assert len(captured) == 1 and captured[0].text == expect


async def test_inbound_poll_creation_becomes_text(
    fake_neonize: dict[str, Any], tmp_path: Path
) -> None:
    captured: list[Inbound] = []

    async def dispatch(_sender_id: str, _name: str, inbound: Inbound, _send: Send) -> None:
        captured.append(inbound)

    msg = _msg()
    msg.Message.pollCreationMessage = SimpleNamespace(
        name="Lunch?",
        options=[SimpleNamespace(optionName="Pizza"), SimpleNamespace(optionName="Sushi")],
    )
    client = WhatsAppWebConnector(tmp_path / "wa.db", dispatch).build_client()
    await client.handlers[fake_neonize["MessageEv"]](client, msg)

    assert len(captured) == 1
    assert "Lunch?" in captured[0].text and "Pizza" in captured[0].text


async def test_unsupported_type_is_dropped(fake_neonize: dict[str, Any], tmp_path: Path) -> None:
    # A message with no text, file, voice, or known special type produces nothing → not dispatched.
    captured: list[Inbound] = []

    async def dispatch(_sender_id: str, _name: str, inbound: Inbound, _send: Send) -> None:
        captured.append(inbound)

    client = WhatsAppWebConnector(tmp_path / "wa.db", dispatch).build_client()
    await client.handlers[fake_neonize["MessageEv"]](client, _msg())  # empty message

    assert captured == []


async def test_start_stops_client_on_cancel(fake_neonize: dict[str, Any], tmp_path: Path) -> None:
    # The shutdown hang: a cancelled start() must call stop() — disconnect() alone leaves
    # neonize's blocking Go call parked in a non-daemon thread that wedges interpreter exit.
    connector = WhatsAppWebConnector(tmp_path / "wa.db", _noop_dispatch)
    captured: dict[str, _FakeClient] = {}
    real_build = connector.build_client

    def _capture() -> _FakeClient:
        client = real_build()
        captured["client"] = client
        return client

    connector.build_client = _capture  # type: ignore[method-assign]

    task = asyncio.create_task(connector.start())
    await asyncio.sleep(0)  # let it connect + reach idle()
    client = captured["client"]
    assert client.connected is True

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert client.stopped is True  # finally ran stop() on the way out


async def test_pair_returns_true_when_connected_event_fires(
    fake_neonize: dict[str, Any], tmp_path: Path
) -> None:
    connector = WhatsAppWebConnector(tmp_path / "wa.db", _noop_dispatch)
    real_build = connector.build_client

    def _capture() -> _FakeClient:
        client = real_build()

        async def connect() -> None:
            client.connected = True
            await client.handlers[fake_neonize["ConnectedEv"]](client, object())

        client.connect = connect  # type: ignore[method-assign]
        return client

    connector.build_client = _capture  # type: ignore[method-assign]

    assert await connector.pair(timeout_s=2) is True


async def test_pair_returns_false_on_timeout(fake_neonize: dict[str, Any], tmp_path: Path) -> None:
    connector = WhatsAppWebConnector(tmp_path / "wa.db", _noop_dispatch)

    assert await connector.pair(timeout_s=0.01) is False


async def test_stop_client_falls_back_to_disconnect() -> None:
    # A client exposing only disconnect() (no stop) must still be torn down.
    from gaia.connectors.whatsapp_web import _stop_client

    class _OnlyDisconnect:
        def __init__(self) -> None:
            self.disconnected = False

        async def disconnect(self) -> None:
            self.disconnected = True

    client = _OnlyDisconnect()
    await _stop_client(client)
    assert client.disconnected is True


# --- group-chat gating ---------------------------------------------------------------

from gaia.config import GroupTrigger  # noqa: E402
from gaia.connectors.whatsapp_web import _group_decision  # noqa: E402

_BOT = "bot@s.whatsapp.net"
_BOT_LID = "lidbot@lid"
_BOT_IDS = {"bot", "lidbot"}  # the bot's own number-parts (phone + @lid)


def _gt(**kw: Any) -> GroupTrigger:
    return GroupTrigger(**kw)


def test_group_decision_dm_always_passes() -> None:
    dm = SimpleNamespace(IsGroup=False)
    assert _group_decision(_msg(conversation="hi"), dm, _BOT_IDS, _gt()) is True


def test_group_decision_requires_addressing() -> None:
    # Who is *allowed* is the role system's job; here we only gate on being addressed.
    src = _group_source("555@s.whatsapp.net")
    # mentioned → handle
    assert _group_decision(_msg(mentioned=(_BOT,)), src, _BOT_IDS, _gt()) is True
    # not addressed → drop (mention_only default)
    assert _group_decision(_msg(conversation="hello"), src, _BOT_IDS, _gt()) is False
    # mention of someone else → still not addressed
    assert _group_decision(_msg(mentioned=("999@s.whatsapp.net",)), src, _BOT_IDS, _gt()) is False


def test_group_decision_matches_lid_mention() -> None:
    # WhatsApp often carries the bot's mention as its @lid, not its phone JID.
    src = _group_source("555@s.whatsapp.net")
    assert _group_decision(_msg(mentioned=(_BOT_LID,)), src, _BOT_IDS, _gt()) is True


def test_group_decision_reply_to_gaia_counts_as_addressed() -> None:
    src = _group_source("555@s.whatsapp.net")
    assert _group_decision(_msg(reply_author=_BOT), src, _BOT_IDS, _gt()) is True
    # a reply to someone else is not addressing Gaia
    assert _group_decision(_msg(reply_author="333@s.whatsapp.net"), src, _BOT_IDS, _gt()) is False


def test_group_decision_respond_in_groups_off() -> None:
    src = _group_source("555@s.whatsapp.net")
    assert (
        _group_decision(_msg(mentioned=(_BOT,)), src, _BOT_IDS, _gt(respond_in_groups=False))
        is False
    )


def test_group_decision_mention_only_off_passes_any() -> None:
    src = _group_source("555@s.whatsapp.net")
    # mention_only off → any group message is considered (role gate decides who)
    assert _group_decision(_msg(conversation="hey"), src, _BOT_IDS, _gt(mention_only=False)) is True


async def test_group_message_dropped_when_not_addressed(
    fake_neonize: dict[str, Any], tmp_path: Path
) -> None:
    seen: list[str] = []

    async def dispatch(_s: str, _n: str, inbound: Inbound, _send: Send) -> None:
        seen.append(inbound.text)

    connector = WhatsAppWebConnector(tmp_path / "wa.db", dispatch)
    client = connector.build_client()
    msg = _msg(conversation="just chatting")  # no mention
    msg.Info.MessageSource = _group_source("555@s.whatsapp.net")

    await client.handlers[fake_neonize["MessageEv"]](client, msg)
    assert seen == []  # gaia stayed silent — not addressed


async def test_group_message_handled_when_mentioned(
    fake_neonize: dict[str, Any], tmp_path: Path
) -> None:
    seen: list[str] = []

    async def dispatch(_s: str, _n: str, inbound: Inbound, send: Send) -> None:
        seen.append(inbound.text)
        await send("on it")

    connector = WhatsAppWebConnector(tmp_path / "wa.db", dispatch)
    client = connector.build_client()
    msg = _msg(conversation="@gaia status", mentioned=(_BOT,))
    msg.Info.MessageSource = _group_source("555@s.whatsapp.net")

    await client.handlers[fake_neonize["MessageEv"]](client, msg)
    assert seen == ["@gaia status"] and [t for t, _ in client.replies] == ["on it"]


# --- read receipts + typing indicator ------------------------------------------------


async def test_marks_read_and_shows_typing(fake_neonize: dict[str, Any], tmp_path: Path) -> None:
    # When Gaia starts a turn: blue-tick the inbound, type while working, clear when done.
    async def dispatch(_s: str, _n: str, _inbound: Inbound, send: Send) -> None:
        await send("hi back")

    client = WhatsAppWebConnector(tmp_path / "wa.db", dispatch).build_client()
    await client.handlers[fake_neonize["MessageEv"]](client, _msg(conversation="hello"))

    assert client.reads == [("MSGID", "chat-jid", "sender-jid", "read")]  # blue tick
    # composing first (text media), paused last — the indicator went up then cleared.
    assert client.presence[0] == ("chat-jid", "composing", "text")
    assert client.presence[-1] == ("chat-jid", "paused", "text")
    assert [text for text, _ in client.replies] == ["hi back"]


async def test_voice_turn_shows_recording_audio(
    fake_neonize: dict[str, Any], tmp_path: Path, cache_dir: Path
) -> None:
    sys.modules["neonize.aioze.client"].NewAClient = _DownloadClient  # type: ignore[attr-defined]

    async def dispatch(_s: str, _n: str, _inbound: Inbound, send: Send) -> None:
        await send("spoken answer")

    connector = WhatsAppWebConnector(tmp_path / "wa.db", dispatch, transcriber=_FakeTranscriber())
    client = connector.build_client()
    await client.handlers[fake_neonize["MessageEv"]](client, _voice_msg())

    # voice-in → the indicator uses the audio media ("recording audio…").
    assert any(state == "composing" and media == "audio" for _c, state, media in client.presence)


async def test_presence_announced_on_connect(fake_neonize: dict[str, Any], tmp_path: Path) -> None:
    client = WhatsAppWebConnector(tmp_path / "wa.db", _noop_dispatch).build_client()
    await client.handlers[fake_neonize["ConnectedEv"]](client, object())
    assert client.availability == ["available"]


async def test_presence_disabled_makes_no_calls(
    fake_neonize: dict[str, Any], tmp_path: Path
) -> None:
    async def dispatch(_s: str, _n: str, _inbound: Inbound, send: Send) -> None:
        await send("ok")

    connector = WhatsAppWebConnector(tmp_path / "wa.db", dispatch, show_active=False)
    client = connector.build_client()
    await client.handlers[fake_neonize["ConnectedEv"]](client, object())
    await client.handlers[fake_neonize["MessageEv"]](client, _msg(conversation="hello"))

    assert client.reads == [] and client.presence == [] and client.availability == []
