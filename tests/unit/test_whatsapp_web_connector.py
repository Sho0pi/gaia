"""Unit tests for the neonize-backed regular-account WhatsApp connector.

neonize ships a native whatsmeow binary we don't want in unit tests, so the
``neonize.aioze`` modules are faked in ``sys.modules`` and the connector's lazy
import picks them up. This verifies the wiring (handler bridge, text extraction,
reply) without touching the real library.
"""

from __future__ import annotations

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
        Info=SimpleNamespace(MessageSource=SimpleNamespace(Chat="chat-jid")),
    )


class _FakeClient:
    def __init__(self, name: str) -> None:
        self.name = name
        self.handlers: dict[Any, Any] = {}
        self.replies: list[tuple[str, Any]] = []
        self.images: list[tuple[Any, str, str | None]] = []
        self.connected = False
        self.stopped = False

    async def connect(self) -> None:
        self.connected = True

    async def stop(self) -> None:  # pair() tears the client down through this
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

    for name, mod in {
        "neonize": ModuleType("neonize"),
        "neonize.aioze": ModuleType("neonize.aioze"),
        "neonize.aioze.client": client_mod,
        "neonize.aioze.events": events_mod,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)

    return {"MessageEv": message_ev}


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


def test_build_client_creates_session_dir(fake_neonize: dict[str, Any], tmp_path: Path) -> None:
    db = tmp_path / "nested" / "whatsapp.db"

    async def handler(_text: str) -> str:
        return "ok"

    WhatsAppWebConnector(db, handler).build_client()

    assert db.parent.is_dir()


async def test_inbound_message_routed_to_handler(
    fake_neonize: dict[str, Any], tmp_path: Path
) -> None:
    seen: list[str] = []

    async def handler(text: str, send: Send) -> None:
        seen.append(text)
        # Stream two replies to prove the sink can fan out one inbound to many.
        await send(f"echo:{text}")
        await send("again")

    client = WhatsAppWebConnector(tmp_path / "wa.db", handler).build_client()
    message = _msg(conversation="ping")

    await client.handlers[fake_neonize["MessageEv"]](client, message)

    assert seen == ["ping"]
    assert client.replies == [("echo:ping", message), ("again", message)]


async def test_media_reply_sent_as_image(fake_neonize: dict[str, Any], tmp_path: Path) -> None:
    from gaia.connectors.base import Media

    async def handler(_text: str, send: Send) -> None:
        await send("here:")  # a text reply, then the image
        await send(Media(Path("/tmp/shot.png"), caption="screenshot"))

    client = WhatsAppWebConnector(tmp_path / "wa.db", handler).build_client()
    await client.handlers[fake_neonize["MessageEv"]](client, _msg(conversation="shot"))

    assert [text for text, _ in client.replies] == ["here:"]  # text reply still sent
    assert client.images == [("chat-jid", "/tmp/shot.png", "screenshot")]  # image via send_image


async def test_empty_message_is_ignored(fake_neonize: dict[str, Any], tmp_path: Path) -> None:
    async def handler(_text: str, _send: Send) -> None:  # pragma: no cover - must not run
        raise AssertionError("handler called on empty message")

    client = WhatsAppWebConnector(tmp_path / "wa.db", handler).build_client()

    await client.handlers[fake_neonize["MessageEv"]](client, _msg())

    assert client.replies == []


async def test_pair_returns_true_when_connected_event_fires(
    fake_neonize: dict[str, Any], tmp_path: Path
) -> None:
    async def handler(_text: str, _send: Send) -> None:  # pragma: no cover - never called
        raise AssertionError

    connector = WhatsAppWebConnector(tmp_path / "wa.db", handler)

    class _PairingClient(_FakeClient):
        async def connect(self) -> None:
            await super().connect()
            # Simulate the phone scanning the QR right after connect: fire the
            # registered ConnectedEv handler, which must set the connector's event.
            for event_type, fn in self.handlers.items():
                if event_type.__name__ == "C":  # the fake ConnectedEv
                    await fn(self, object())

    sys.modules["neonize.aioze.client"].NewAClient = _PairingClient  # type: ignore[attr-defined]

    paired = await connector.pair(timeout_s=2)

    assert paired is True


async def test_pair_times_out_false(fake_neonize: dict[str, Any], tmp_path: Path) -> None:
    async def handler(_text: str, _send: Send) -> None:  # pragma: no cover - never called
        raise AssertionError

    connector = WhatsAppWebConnector(tmp_path / "wa.db", handler)

    paired = await connector.pair(timeout_s=0.05)  # nothing ever fires ConnectedEv

    assert paired is False
