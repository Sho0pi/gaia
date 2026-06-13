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

from gaia.connectors.base import Send
from gaia.connectors.whatsapp_web import WhatsAppWebConnector, _message_text


def _msg(*, conversation: str = "", extended: str = "", quoted: str = "") -> SimpleNamespace:
    """Build a fake neonize MessageEv with the fields the connector reads.

    ``quoted`` populates extendedTextMessage.contextInfo.quotedMessage (a reply).
    """
    quoted_message = (
        SimpleNamespace(conversation=quoted, extendedTextMessage=SimpleNamespace(text=""))
        if quoted
        else None
    )
    context_info = SimpleNamespace(quotedMessage=quoted_message)
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


class _FakeClient:
    def __init__(self, name: str) -> None:
        self.name = name
        self.handlers: dict[Any, Any] = {}
        self.replies: list[tuple[str, Any]] = []
        self.images: list[tuple[Any, str, str | None]] = []
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


async def _noop_dispatch(_sender_id: str, _name: str, _text: str, _send: Send) -> None:
    return None


def test_build_client_creates_session_dir(fake_neonize: dict[str, Any], tmp_path: Path) -> None:
    db = tmp_path / "nested" / "whatsapp.db"

    WhatsAppWebConnector(db, _noop_dispatch).build_client()

    assert db.parent.is_dir()


async def test_inbound_message_routed_to_dispatch(
    fake_neonize: dict[str, Any], tmp_path: Path
) -> None:
    seen: list[str] = []

    async def dispatch(_sender_id: str, _name: str, text: str, send: Send) -> None:
        seen.append(text)
        # Stream two replies to prove the sink can fan out one inbound to many.
        await send(f"echo:{text}")
        await send("again")

    client = WhatsAppWebConnector(tmp_path / "wa.db", dispatch).build_client()
    message = _msg(conversation="ping")

    await client.handlers[fake_neonize["MessageEv"]](client, message)

    assert seen == ["ping"]
    assert client.replies == [("echo:ping", message), ("again", message)]


async def test_media_reply_sent_as_image(fake_neonize: dict[str, Any], tmp_path: Path) -> None:
    from gaia.connectors.base import Media

    async def dispatch(_sender_id: str, _name: str, _text: str, send: Send) -> None:
        await send("here:")  # a text reply, then the image
        await send(Media(Path("/tmp/shot.png"), caption="screenshot"))

    client = WhatsAppWebConnector(tmp_path / "wa.db", dispatch).build_client()
    await client.handlers[fake_neonize["MessageEv"]](client, _msg(conversation="shot"))

    assert [text for text, _ in client.replies] == ["here:"]  # text reply still sent
    assert client.images == [("chat-jid", "/tmp/shot.png", "screenshot")]  # image via send_image


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

    async def dispatch(sender_id: str, _name: str, _text: str, _send: Send) -> None:
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
    from gaia.connectors.base import current_chat

    client = WhatsAppWebConnector(tmp_path / "wa.db", _noop_dispatch).build_client()
    msg = _msg(conversation="hi")
    msg.Info.MessageSource = SimpleNamespace(
        Chat=SimpleNamespace(User="12036302byte", Server="g.us"), IsGroup=True
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

    async def dispatch(_sender_id: str, _name: str, text: str, _send: Send) -> None:
        seen.append(text)

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

    async def dispatch(_sender_id: str, _name: str, text: str, _send: Send) -> None:
        seen.append(text)

    connector = WhatsAppWebConnector(tmp_path / "wa.db", dispatch, transcriber=_FakeTranscriber())
    client = connector.build_client()

    await client.handlers[fake_neonize["MessageEv"]](client, _msg(conversation="plain text"))

    assert seen == ["plain text"]  # no [voice message] prefix, no transcription


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


# --- read receipts + typing indicator ------------------------------------------------


async def test_marks_read_and_shows_typing(fake_neonize: dict[str, Any], tmp_path: Path) -> None:
    # When Gaia starts a turn: blue-tick the inbound, type while working, clear when done.
    async def dispatch(_s: str, _n: str, _t: str, send: Send) -> None:
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

    async def dispatch(_s: str, _n: str, _t: str, send: Send) -> None:
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
    async def dispatch(_s: str, _n: str, _t: str, send: Send) -> None:
        await send("ok")

    connector = WhatsAppWebConnector(tmp_path / "wa.db", dispatch, show_active=False)
    client = connector.build_client()
    await client.handlers[fake_neonize["ConnectedEv"]](client, object())
    await client.handlers[fake_neonize["MessageEv"]](client, _msg(conversation="hello"))

    assert client.reads == [] and client.presence == [] and client.availability == []
