"""MessageCoalescer — debounce/merge rapid inbound messages into one turn.

Offline, fast: real (tiny) timers, a fake ``run`` that records each merged turn.
"""

from __future__ import annotations

import asyncio
from typing import Any

from gaia.core.coalesce import MessageCoalescer

_KEY = ("itay", "whatsapp")
_CHAT = ("whatsapp", "itay@s.whatsapp.net")


async def _send(_reply: Any) -> None:
    return None


def _coalescer(
    *, enabled: bool = True, quiet: float = 0.05, max_s: float = 1.0
) -> tuple[MessageCoalescer, list[str]]:
    calls: list[str] = []

    c = MessageCoalescer(
        enabled=lambda: enabled,
        quiet_seconds=lambda: quiet,
        max_seconds=lambda: max_s,
        is_command=lambda t: t.startswith("/"),
    )
    return c, calls


def _run(calls: list[str]) -> Any:
    async def run(merged: str) -> None:
        calls.append(merged)

    return run


async def test_rapid_messages_merge_into_one_turn() -> None:
    c, calls = _coalescer(quiet=0.05, max_s=1.0)
    run = _run(calls)

    async def second() -> None:
        await asyncio.sleep(0.01)  # arrives within the quiet window
        await c.submit(_KEY, "fix: i meant blue", _CHAT, _send, run)

    await asyncio.gather(c.submit(_KEY, "make it red", _CHAT, _send, run), second())

    assert calls == ["make it red\nfix: i meant blue"]  # one turn, joined in order


async def test_messages_apart_are_separate_turns() -> None:
    c, calls = _coalescer(quiet=0.03, max_s=1.0)
    run = _run(calls)

    await c.submit(_KEY, "a", _CHAT, _send, run)  # blocks until its batch runs
    await c.submit(_KEY, "b", _CHAT, _send, run)  # a fresh batch

    assert calls == ["a", "b"]


async def test_typing_holds_the_batch_until_paused() -> None:
    c, calls = _coalescer(quiet=0.05, max_s=2.0)
    run = _run(calls)
    task = asyncio.ensure_future(c.submit(_KEY, "drafting…", _CHAT, _send, run))

    await asyncio.sleep(0.01)
    c.typing(_KEY, True)
    await asyncio.sleep(0.2)  # well past quiet, but still composing
    assert calls == []  # held open

    c.typing(_KEY, False)
    await asyncio.wait_for(task, timeout=1.0)
    assert calls == ["drafting…"]  # fires once typing stops


async def test_cap_fires_even_while_typing() -> None:
    c, calls = _coalescer(quiet=0.05, max_s=0.15)
    run = _run(calls)
    task = asyncio.ensure_future(c.submit(_KEY, "x", _CHAT, _send, run))

    await asyncio.sleep(0.01)
    c.typing(_KEY, True)  # never paused
    await asyncio.wait_for(task, timeout=1.0)  # cap forces the turn anyway

    assert calls == ["x"]


async def test_command_runs_immediately_and_alone() -> None:
    c, calls = _coalescer(quiet=10.0)  # long quiet — proves the command bypasses it
    run = _run(calls)

    await asyncio.wait_for(c.submit(_KEY, "/reset", _CHAT, _send, run), timeout=0.5)

    assert calls == ["/reset"]


async def test_command_flushes_a_pending_batch_first() -> None:
    c, calls = _coalescer(quiet=10.0)
    run = _run(calls)
    pending = asyncio.ensure_future(c.submit(_KEY, "draft", _CHAT, _send, run))

    await asyncio.sleep(0.01)
    await asyncio.wait_for(c.submit(_KEY, "/reset", _CHAT, _send, run), timeout=0.5)
    await pending

    assert calls == ["draft", "/reset"]  # pending flushed, then the command


async def test_disabled_runs_each_message_immediately() -> None:
    c, calls = _coalescer(enabled=False, quiet=10.0)
    run = _run(calls)

    await c.submit(_KEY, "a", _CHAT, _send, run)
    await c.submit(_KEY, "b", _CHAT, _send, run)

    assert calls == ["a", "b"]  # no merging when disabled
