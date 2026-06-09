"""Each built-in command's behaviour, driven with SimpleNamespace fakes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from godpy.commands import default_registry
from godpy.commands.base import CommandContext
from godpy.config import GodConfig


class _FakeMemory:
    def __init__(self, items: list[str] | None = None) -> None:
        self._items = items or []
        self.added: list[Any] = []
        self.forgot = False

    async def add_memory(self, *, app_name: str, user_id: str, memories: list[Any]) -> None:
        self.added.extend(memories)

    async def list_memories(self, *, user_id: str) -> list[str]:
        return list(self._items)

    async def forget(self, *, user_id: str) -> int:
        self.forgot = True
        return len(self._items)


def _ctx(
    *,
    args: str = "",
    memory: Any = None,
    agents: list[str] | None = None,
    handler: Any = None,
) -> CommandContext:
    god = SimpleNamespace(
        config=GodConfig(),
        settings=SimpleNamespace(model="gemini-test"),
        memory_service=memory,
        known_agents=lambda: agents or [],
        tools=SimpleNamespace(names=lambda: ["web_fetch", "fs_read"]),
    )
    return CommandContext(
        args=args,
        god=god,
        handler=handler or SimpleNamespace(),
        registry=default_registry(),
        user_id="u1",
        session_id="s1",
    )


def _run(name: str, ctx: CommandContext) -> Any:
    return default_registry().get(name).run(ctx)


async def test_help_lists_every_command() -> None:
    out = await _run("help", _ctx())

    assert out.startswith("Commands:")
    for name in ("/help", "/reset", "/forget", "/remember"):
        assert name in out


async def test_whoami_shows_user_and_memory() -> None:
    out = await _run("whoami", _ctx())

    assert "user: u1" in out and "session: s1" in out
    assert "long-term memory: on" in out


async def test_agents_empty_and_populated() -> None:
    assert "No specialist" in await _run("agents", _ctx())
    assert "- researcher" in await _run("agents", _ctx(agents=["researcher"]))


async def test_status_reports_counts() -> None:
    out = await _run("status", _ctx(agents=["a", "b"]))

    assert "subagents: 2" in out
    assert "tools: 2" in out
    assert "memory: on" in out


async def test_reset_flushes_then_clears_session() -> None:
    calls: list[str] = []
    handler = SimpleNamespace(
        flush=lambda: _async(calls.append("flush")),
        reset_session=lambda: calls.append("reset"),
    )

    out = await _run("reset", _ctx(handler=handler))

    assert calls == ["flush", "reset"]  # persist before clearing
    assert "cleared" in out.lower()


async def test_remember_stores_fact() -> None:
    memory = _FakeMemory()

    out = await _run("remember", _ctx(args="I like teal", memory=memory))

    assert memory.added[0].content.parts[0].text == "I like teal"
    assert "teal" in out


async def test_remember_requires_text_and_memory() -> None:
    assert "Usage" in await _run("remember", _ctx(args="", memory=_FakeMemory()))
    assert "off" in await _run("remember", _ctx(args="x", memory=None))


async def test_memories_lists_or_reports_empty() -> None:
    assert "don't remember" in await _run("memories", _ctx(memory=_FakeMemory()))
    out = await _run("memories", _ctx(memory=_FakeMemory(["likes teal", "owns a cat"])))
    assert "- likes teal" in out and "- owns a cat" in out


async def test_forget_requires_confirmation() -> None:
    memory = _FakeMemory(["a", "b"])

    warn = await _run("forget", _ctx(memory=memory))

    assert "confirm" in warn.lower() and "2 items" in warn
    assert memory.forgot is False  # nothing wiped without the token


async def test_forget_yes_wipes() -> None:
    memory = _FakeMemory(["a", "b"])

    out = await _run("forget", _ctx(args="yes", memory=memory))

    assert memory.forgot is True
    assert "2 items" in out


async def test_memory_commands_handle_disabled_memory() -> None:
    assert "off" in await _run("memories", _ctx(memory=None))
    assert "nothing to forget" in await _run("forget", _ctx(memory=None))


def _async(_value: Any) -> Any:
    async def _coro() -> None:
        return None

    return _coro()
