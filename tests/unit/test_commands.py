"""Each built-in command's behaviour, driven with SimpleNamespace fakes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from gaia.commands import default_registry
from gaia.commands.base import CommandContext
from gaia.config import GaiaConfig


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
    sessions: list[tuple[str, float]] | None = None,
    handler: Any = None,
    missing: dict[str, str] | None = None,
) -> CommandContext:
    gaia = SimpleNamespace(
        config=GaiaConfig(),
        settings=SimpleNamespace(model="gemini-test"),
        memory_service=memory,
        known_souls=lambda: agents or [],
        soul_sessions=SimpleNamespace(active=lambda: sessions or []),
        tools=SimpleNamespace(names=lambda: ["web_fetch", "fs_read"], missing=missing or {}),
        users=SimpleNamespace(get=lambda _uid: None),
    )
    return CommandContext(
        args=args,
        gaia=gaia,
        handler=handler or SimpleNamespace(),
        registry=default_registry(),
        user_id="u1",
        session_id="s1",
    )


def _run(name: str, ctx: CommandContext) -> Any:
    return default_registry().get(name).run(ctx)


def _effort_ctx(args: str, cfg_path: Any, *, provider: str = "openai", model: str = "gpt-5.5"):
    from gaia.config.schema import LLMConfig

    gaia = SimpleNamespace(
        config=GaiaConfig(llm=LLMConfig(provider=provider, model=model)),
        settings=SimpleNamespace(model=model, config_path=cfg_path),
    )
    return CommandContext(
        args=args,
        gaia=gaia,
        handler=SimpleNamespace(),
        registry=default_registry(),
        user_id="u1",
        session_id="s1",
    )


async def test_effort_shows_writes_and_clears(tmp_path: Any) -> None:
    cfg = tmp_path / "gaia.yaml"

    # No arg -> shows current (default).
    assert "(default)" in await _run("effort", _effort_ctx("", cfg))

    # Set -> persisted to yaml.
    out = await _run("effort", _effort_ctx("high", cfg))
    assert "set to 'high'" in out
    assert 'effort: "high"' in cfg.read_text()

    # Clear -> blanks it.
    await _run("effort", _effort_ctx("off", cfg))
    assert 'effort: ""' in cfg.read_text()


async def test_effort_rejects_unknown_level(tmp_path: Any) -> None:
    out = await _run("effort", _effort_ctx("turbo", tmp_path / "gaia.yaml"))
    assert "Unknown effort" in out


async def test_effort_warns_on_nonthinking_model(tmp_path: Any) -> None:
    ctx = _effort_ctx("high", tmp_path / "gaia.yaml", provider="gemini", model="gemini-2.0-flash")
    assert "no reasoning dial" in await _run("effort", ctx)


async def test_help_lists_every_command() -> None:
    out = await _run("help", _ctx())

    assert out.startswith("Commands:")
    for name in ("/help", "/reset", "/forget", "/remember"):
        assert name in out


async def test_whoami_shows_user_and_memory() -> None:
    out = await _run("whoami", _ctx())

    assert "user: u1" in out and "session: s1" in out
    assert "long-term memory: on" in out


async def test_whoami_shows_effort_only_when_set() -> None:
    from gaia.config.schema import LLMConfig

    assert "(effort:" not in await _run("whoami", _ctx())  # blank -> no clutter

    ctx = _ctx()
    ctx.gaia.config = GaiaConfig(llm=LLMConfig(effort="high"))
    assert "(effort: high)" in await _run("whoami", ctx)


async def test_souls_empty_and_populated() -> None:
    assert "No souls" in await _run("soul", _ctx())
    assert "- researcher" in await _run("soul", _ctx(agents=["researcher"]))


async def test_souls_lists_live_warm_sessions() -> None:
    out = await _run(
        "soul",
        _ctx(agents=["frontend_developer"], sessions=[("frontend_developer/pasta", 30.0)]),
    )
    assert "Live now (1)" in out
    assert "frontend_developer/pasta" in out and "just now" in out


async def test_status_reports_counts() -> None:
    out = await _run("status", _ctx(agents=["a", "b"]))

    assert "subagents: 2" in out
    assert "tools: 2" in out
    assert "memory: on" in out
    assert "disabled tools:" not in out  # nothing missing → no line


async def test_status_shows_effort_only_when_set() -> None:
    from gaia.config.schema import LLMConfig

    assert "(effort:" not in await _run("status", _ctx())  # blank -> no clutter

    ctx = _ctx()
    ctx.gaia.config = GaiaConfig(llm=LLMConfig(effort="high"))
    assert "(effort: high)" in await _run("status", ctx)


async def test_status_lists_disabled_tools() -> None:
    out = await _run("status", _ctx(missing={"fs_glob": "'fd' not on PATH"}))

    assert "disabled tools: fs_glob ('fd' not on PATH)" in out


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
    assert "don't remember" in await _run("memory", _ctx(memory=_FakeMemory()))
    out = await _run("memory", _ctx(memory=_FakeMemory(["likes teal", "owns a cat"])))
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
    assert "off" in await _run("memory", _ctx(memory=None))
    assert "nothing to forget" in await _run("forget", _ctx(memory=None))


def _async(_value: Any) -> Any:
    async def _coro() -> None:
        return None

    return _coro()
