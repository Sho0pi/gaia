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

from godpy.connectors.base import Send
from godpy.connectors.whatsapp_web import WhatsAppWebConnector, _message_text


def _msg(*, conversation: str = "", extended: str = "") -> SimpleNamespace:
    """Build a fake neonize MessageEv with the fields the connector reads."""
    return SimpleNamespace(
        Message=SimpleNamespace(
            conversation=conversation,
            extendedTextMessage=SimpleNamespace(text=extended),
        ),
        Info=SimpleNamespace(MessageSource=SimpleNamespace(Chat="chat-jid")),
    )


class _FakeClient:
    def __init__(self, name: str) -> None:
        self.name = name
        self.handlers: dict[Any, Any] = {}
        self.replies: list[tuple[str, Any]] = []
        self.images: list[tuple[Any, str, str | None]] = []

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
    from godpy.connectors.base import Media

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
