"""Debounce rapid inbound messages into one turn.

People fire two messages back-to-back — a typo fix, a forgotten detail, or (the epic's
goal) an image then "what's that?". Running each as its own turn answers the first one
half-blind. :class:`MessageCoalescer` instead **buffers** messages per conversation and
**waits** a moment — longer while the user is still typing, never past a cap — then runs a
single turn over the merged text.

Design notes:

* **Debounce before start, never interrupt.** A message that arrives while a turn is
  already running forms the *next* batch; we don't cancel a running reply (its tool
  side-effects can't be undone). A per-key :class:`asyncio.Lock` serialises turns.
* **The caller awaits the merged turn.** :meth:`submit` blocks until the batch it joined
  has run, so a connector's "Gaia is typing" bracket (``await dispatch``) naturally spans
  the whole batch — no connector change needed for the core.
* **Typing is an extender.** Channels that report inbound "composing" (WhatsApp) call
  :meth:`typing`; while active the quiet gap is ignored (up to the cap). Channels that
  can't (Telegram bots, CLI) simply rely on the quiet/cap timers.
* **Commands aren't merged.** A slash command flushes any pending batch and runs alone.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from gaia.logs import log_event

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.connectors.base import Send

logger = logging.getLogger(__name__)

#: Run one turn over the merged text (a closure that already holds the reply ``send``).
Run = Callable[[str], Awaitable[None]]


@dataclass
class _Batch:
    """One accumulating batch for a conversation key (until it fires)."""

    parts: list[str]
    chat: tuple[str, str]  # current_chat to restore inside the (later) turn
    send: Send
    run: Run
    started: float  # loop time of the first message (the cap is measured from here)
    last: float  # loop time of the most recent message (the quiet gap is measured from here)
    future: asyncio.Future[None]
    nudge: asyncio.Event = field(default_factory=asyncio.Event)
    typing: bool = False
    force: bool = False  # flush now (shutdown, or a command needs the batch out of the way)
    task: asyncio.Task[None] | None = None  # the debounce task (held so it isn't GC'd)


class MessageCoalescer:
    """Buffer + debounce inbound messages per conversation key, run one merged turn."""

    def __init__(
        self,
        *,
        enabled: Callable[[], bool],
        quiet_seconds: Callable[[], float],
        max_seconds: Callable[[], float],
        is_command: Callable[[str], bool],
    ) -> None:
        # The three knobs are read live (callables) so gaia.yaml hot-reload applies.
        self._enabled = enabled
        self._quiet = quiet_seconds
        self._max = max_seconds
        self._is_command = is_command
        self._pending: dict[tuple[str, str], _Batch] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _lock(self, key: tuple[str, str]) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def submit(
        self, key: tuple[str, str], text: str, chat: tuple[str, str], send: Send, run: Run
    ) -> None:
        """Add ``text`` to ``key``'s batch and return once that batch's turn has run."""
        # Commands and the disabled path bypass batching: flush whatever's pending, run now.
        if not self._enabled() or self._is_command(text):
            await self._flush(key)
            async with self._lock(key):
                await self._invoke(chat, run, text)
            return

        batch = self._pending.get(key)
        if batch is None:
            now = asyncio.get_running_loop().time()
            batch = _Batch(
                parts=[text],
                chat=chat,
                send=send,
                run=run,
                started=now,
                last=now,
                future=asyncio.get_running_loop().create_future(),
            )
            self._pending[key] = batch
            batch.task = asyncio.ensure_future(self._debounce(key, batch))
        else:
            batch.parts.append(text)
            batch.last = asyncio.get_running_loop().time()
            batch.nudge.set()
        await asyncio.shield(batch.future)

    def typing(self, key: tuple[str, str], active: bool) -> None:
        """Note that the user is (still) composing — extends the wait, up to the cap."""
        batch = self._pending.get(key)
        if batch is not None:
            batch.typing = active
            batch.nudge.set()

    async def flush_all(self) -> None:
        """Fire every pending batch now and wait for them (shutdown)."""
        for key in list(self._pending):
            await self._flush(key)

    async def _flush(self, key: tuple[str, str]) -> None:
        batch = self._pending.get(key)
        if batch is None:
            return
        batch.force = True
        batch.nudge.set()
        await asyncio.shield(batch.future)

    async def _debounce(self, key: tuple[str, str], batch: _Batch) -> None:
        """Wait out the quiet gap / cap (extended while typing), then run the merged turn."""
        loop = asyncio.get_running_loop()
        while not batch.force:
            now = loop.time()
            cap_left = self._max() - (now - batch.started)
            if cap_left <= 0:
                break
            quiet_left = self._quiet() - (now - batch.last)
            wait = cap_left if batch.typing else min(quiet_left, cap_left)
            if wait <= 0:
                break
            batch.nudge.clear()
            try:
                await asyncio.wait_for(batch.nudge.wait(), timeout=wait)
            except TimeoutError:
                if not batch.typing:  # quiet gap elapsed and not typing -> fire
                    break
            # else: woken by a new message / typing change -> re-evaluate the loop

        # Seal: new messages from here start a fresh batch.
        if self._pending.get(key) is batch:
            del self._pending[key]
        merged = "\n".join(batch.parts)
        if len(batch.parts) > 1:  # observable signal that messages were actually merged
            log_event("messages_coalesced", user=key[0], channel=key[1], count=len(batch.parts))
        try:
            async with self._lock(key):  # serialise turns per conversation
                await self._invoke(batch.chat, batch.run, merged)
        finally:
            if not batch.future.done():
                batch.future.set_result(None)

    async def _invoke(self, chat: tuple[str, str], run: Run, text: str) -> None:
        """Run one turn with ``current_chat`` restored (it ran later, in this task)."""
        from gaia.connectors.base import current_chat

        current_chat.set(chat)
        await run(text)
