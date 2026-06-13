"""message_user: resolve a recipient (id/name/raw) and send via the live connector."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from gaia.connectors.base import current_chat
from gaia.tools.message import make_message_user
from gaia.users import UserStore


class _FakeConnector:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []

    async def send_to(self, chat: str, reply: Any) -> None:
        self.sent.append((chat, reply))


def _gaia(tmp_path: Path, connectors: dict[str, Any]) -> Any:
    return SimpleNamespace(users=UserStore(tmp_path / "users.json"), connectors=connectors)


async def test_sends_to_known_user_by_id(tmp_path: Path) -> None:
    wa = _FakeConnector()
    gaia = _gaia(tmp_path, {"whatsapp": wa})
    gaia.users.register("whatsapp", "972@s.whatsapp.net", "Grace", role="user")

    tool = make_message_user(gaia.users, gaia.connectors)
    out = await tool("grace", "I love you")

    assert out["status"] == "success"
    assert wa.sent == [("972@s.whatsapp.net", "I love you")]


async def test_resolves_by_display_name(tmp_path: Path) -> None:
    wa = _FakeConnector()
    gaia = _gaia(tmp_path, {"whatsapp": wa})
    gaia.users.register("whatsapp", "972@s.whatsapp.net", "Grace", role="user")

    out = await make_message_user(gaia.users, gaia.connectors)("Grace", "hi")

    assert out["status"] == "success"
    assert wa.sent[0][0] == "972@s.whatsapp.net"


async def test_raw_phone_uses_given_channel(tmp_path: Path) -> None:
    wa = _FakeConnector()
    gaia = _gaia(tmp_path, {"whatsapp": wa})

    out = await make_message_user(gaia.users, gaia.connectors)(
        "111@s.whatsapp.net", "yo", channel="whatsapp"
    )

    assert out["status"] == "success"
    assert wa.sent == [("111@s.whatsapp.net", "yo")]


async def test_raw_phone_infers_single_live_channel_and_normalizes(tmp_path: Path) -> None:
    # The reported bug: a phone with no channel (and no ambient chat, as at cron-fire
    # time) must still send — inferred from the only live connector. Formatting stripped.
    wa = _FakeConnector()
    gaia = _gaia(tmp_path, {"whatsapp": wa})
    current_chat.set(("", ""))  # cron-fire: no ambient channel

    out = await make_message_user(gaia.users, gaia.connectors)("+972 50-123-4567", "on my way")

    assert out["status"] == "success"
    assert wa.sent == [("972501234567", "on my way")]


async def test_ambiguous_channel_errors_clearly(tmp_path: Path) -> None:
    gaia = _gaia(tmp_path, {"whatsapp": _FakeConnector(), "telegram": _FakeConnector()})
    current_chat.set(("", ""))

    out = await make_message_user(gaia.users, gaia.connectors)("0501234567", "hi")

    assert out["status"] == "error"
    assert "which channel" in out["error_message"]


async def test_channel_not_running_is_clear_error(tmp_path: Path) -> None:
    gaia = _gaia(tmp_path, {})  # no live connectors (outside the daemon)
    gaia.users.register("whatsapp", "972@s.whatsapp.net", "Grace", role="user")

    out = await make_message_user(gaia.users, gaia.connectors)("grace", "hi")

    assert out["status"] == "error"
    assert "not running" in out["error_message"]


async def test_no_live_channel_errors(tmp_path: Path) -> None:
    gaia = _gaia(tmp_path, {})  # nothing running and no channel hint
    current_chat.set(("", ""))

    out = await make_message_user(gaia.users, gaia.connectors)("nobody", "hi")

    assert out["status"] == "error"
    assert "which channel" in out["error_message"]


async def test_empty_text_rejected(tmp_path: Path) -> None:
    out = await make_message_user(UserStore(tmp_path / "users.json"), {})("grace", "   ")

    assert out["status"] == "error"
    assert "empty" in out["error_message"]
