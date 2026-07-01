"""message_user: resolve a recipient (id/name/raw/memory) and send via the live connector."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from gaia.connectors.base import current_chat
from gaia.tools.message import make_message_user
from gaia.users import UserStore

#: A stand-in ADK tool_context (the tool only reads ``user_id`` off it, for memory lookups).
_CTX = SimpleNamespace(user_id="itay")


class _FakeConnector:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []

    async def send_to(self, chat: str, reply: Any) -> None:
        self.sent.append((chat, reply))


def _memory(*facts: str) -> Any:
    """A fake memory service whose search returns ``facts`` as memory entries."""

    async def search_memory(*, app_name: str, user_id: str, query: str) -> Any:
        memories = [
            SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text=fact)]))
            for fact in facts
        ]
        return SimpleNamespace(memories=memories)

    return SimpleNamespace(search_memory=search_memory)


def _tool(tmp_path: Path, connectors: dict[str, Any], memory: Any = None) -> Any:
    users = UserStore(tmp_path / "users.json")
    return users, make_message_user(users, connectors, lambda: memory)


async def test_sends_to_known_user_by_id(tmp_path: Path) -> None:
    wa = _FakeConnector()
    users, tool = _tool(tmp_path, {"whatsapp": wa})
    users.register("whatsapp", "972@s.whatsapp.net", "Grace", role="user")

    out = await tool("grace", "I love you", tool_context=_CTX)

    assert out["status"] == "success"
    assert wa.sent == [("972@s.whatsapp.net", "I love you")]


async def test_resolves_by_display_name(tmp_path: Path) -> None:
    wa = _FakeConnector()
    users, tool = _tool(tmp_path, {"whatsapp": wa})
    users.register("whatsapp", "972@s.whatsapp.net", "Grace", role="user")

    out = await tool("Grace", "hi", tool_context=_CTX)

    assert out["status"] == "success"
    assert wa.sent[0][0] == "972@s.whatsapp.net"


async def test_raw_phone_uses_given_channel(tmp_path: Path) -> None:
    wa = _FakeConnector()
    _, tool = _tool(tmp_path, {"whatsapp": wa})

    out = await tool("111@s.whatsapp.net", "yo", channel="whatsapp", tool_context=_CTX)

    assert out["status"] == "success"
    assert wa.sent == [("111@s.whatsapp.net", "yo")]


async def test_raw_phone_infers_single_live_channel_and_normalizes(tmp_path: Path) -> None:
    wa = _FakeConnector()
    _, tool = _tool(tmp_path, {"whatsapp": wa})
    current_chat.set(("", ""))  # cron-fire: no ambient channel

    out = await tool("+972 50-123-4567", "on my way", tool_context=_CTX)

    assert out["status"] == "success"
    assert wa.sent == [("972501234567", "on my way")]


async def test_ambiguous_channel_errors_clearly(tmp_path: Path) -> None:
    _, tool = _tool(tmp_path, {"whatsapp": _FakeConnector(), "telegram": _FakeConnector()})
    current_chat.set(("", ""))

    out = await tool("0501234567", "hi", tool_context=_CTX)

    assert out["status"] == "error"
    assert "which channel" in out["error_message"]


async def test_channel_not_running_is_clear_error(tmp_path: Path) -> None:
    users, tool = _tool(tmp_path, {})  # no live connectors (outside the daemon)
    users.register("whatsapp", "972@s.whatsapp.net", "Grace", role="user")

    out = await tool("grace", "hi", tool_context=_CTX)

    assert out["status"] == "error"
    assert "not running" in out["error_message"]


async def test_no_live_channel_errors(tmp_path: Path) -> None:
    _, tool = _tool(tmp_path, {})  # nothing running and no channel hint
    current_chat.set(("", ""))

    out = await tool("0501234567", "hi", tool_context=_CTX)

    assert out["status"] == "error"
    assert "which channel" in out["error_message"]


async def test_empty_text_rejected(tmp_path: Path) -> None:
    _, tool = _tool(tmp_path, {})

    out = await tool("grace", "   ", tool_context=_CTX)

    assert out["status"] == "error"
    assert "empty" in out["error_message"]


# --- memory-backed resolution: a relationship/nickname -> a number from memory -----------


async def test_label_resolves_to_number_from_memory(tmp_path: Path) -> None:
    wa = _FakeConnector()
    memory = _memory("User's girlfriend is Mei; her number is +852 5550 1234.")
    _, tool = _tool(tmp_path, {"whatsapp": wa}, memory)
    current_chat.set(("", ""))

    out = await tool("girlfriend", "Goodnight", tool_context=_CTX)

    assert out["status"] == "success"
    assert wa.sent == [("85255501234", "Goodnight")]  # single clear match -> auto-send


async def test_label_with_multiple_numbers_asks(tmp_path: Path) -> None:
    memory = _memory("Mei: +852 5550 1234", "old Mei number +852 1111 2222")
    _, tool = _tool(tmp_path, {"whatsapp": _FakeConnector()}, memory)
    current_chat.set(("", ""))

    out = await tool("girlfriend", "hi", tool_context=_CTX)

    assert out["status"] == "error"
    assert "more than one number" in out["error_message"]


async def test_label_unknown_with_no_memory_match(tmp_path: Path) -> None:
    memory = _memory("User likes football.")  # no phone number anywhere
    _, tool = _tool(tmp_path, {"whatsapp": _FakeConnector()}, memory)
    current_chat.set(("", ""))

    out = await tool("girlfriend", "hi", tool_context=_CTX)

    assert out["status"] == "error"
    assert "nothing in memory" in out["error_message"]


async def test_label_without_memory_service(tmp_path: Path) -> None:
    _, tool = _tool(tmp_path, {"whatsapp": _FakeConnector()}, None)  # memory off

    out = await tool("girlfriend", "hi", tool_context=_CTX)

    assert out["status"] == "error"
    assert "don't have a contact" in out["error_message"]
