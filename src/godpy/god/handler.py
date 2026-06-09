"""Bridge a plain-text message to God's ADK root agent and back to text.

Connectors speak :data:`~godpy.connectors.base.Handler`; ADK speaks ``Runner``
events over a session. :class:`GodHandler` is the thin glue between them. The ADK
imports are deferred so importing godpy stays cheap and the connectors remain
unit-testable without a model backend.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from godpy import constants
from godpy.connectors.base import Handler, Send
from godpy.logs import log_event

if TYPE_CHECKING:  # pragma: no cover - typing only
    from godpy.god.agent import God


class GodHandler:
    """Runs inbound text through God's ADK root agent and returns the reply text.

    The ADK ``Runner`` and its session are expensive to build and hold the running
    conversation, so they're created once on the first message and kept on the
    instance (``self._runner``); later messages reuse them, which is what gives the
    bot memory within a process. One ``GodHandler`` == one conversation.
    """

    def __init__(
        self, god: God, *, user_id: str = "god-user", session_id: str = "god-session"
    ) -> None:
        self._god = god
        self._user_id = user_id
        self._session_id = session_id
        self._runner: Any | None = None
        # Auto-ingest buffer: turns accumulate here and flush in batches (by count or
        # age) so mem0's per-add extraction LLM call fires once per batch, not per turn.
        self._buffer: list[Any] = []
        self._buffer_started: float | None = None

    async def _ensure_runner(self) -> Any:
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        from godpy.god.plugins import ToolLoggingPlugin
        from godpy.tools import SELF_LOGGING_TOOLS

        if self._runner is None:
            session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
            await session_service.create_session(
                app_name=constants.APP_NAME, user_id=self._user_id, session_id=self._session_id
            )
            self._runner = Runner(
                app_name=constants.APP_NAME,
                agent=self._god.build_root_agent(),
                session_service=session_service,
                memory_service=self._god.memory_service,
                plugins=[ToolLoggingPlugin(SELF_LOGGING_TOOLS)],
            )
        return self._runner

    async def __call__(self, text: str, send: Send) -> None:
        from google.genai import types

        log_event("message_in", user=self._user_id, session=self._session_id, chars=len(text))
        runner = await self._ensure_runner()
        content = types.Content(role="user", parts=[types.Part(text=text)])

        turn_events: list[Any] = []
        async for event in runner.run_async(
            user_id=self._user_id, session_id=self._session_id, new_message=content
        ):
            turn_events.append(event)
            # A model turn can carry several parts (text, function calls, inline
            # data). Stream each text part of the final answer as its own reply
            # instead of joining them, so one inbound message can fan out to many.
            if event.is_final_response() and event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        log_event("message_out", user=self._user_id, chars=len(part.text))
                        await send(part.text)

        await self._buffer_turn(turn_events)

    async def _buffer_turn(self, events: list[Any]) -> None:
        """Add a turn to the auto-ingest buffer, flushing when it's full or stale."""
        service = self._god.memory_service
        if service is None or not self._god.config.memory.auto_ingest:
            return
        if not events:
            return
        if self._buffer_started is None:
            self._buffer_started = time.monotonic()
        self._buffer.extend(events)

        memory = self._god.config.memory
        age = time.monotonic() - self._buffer_started
        if len(self._buffer) >= memory.ingest_batch_size or age >= memory.ingest_interval_seconds:
            # The reply is already streamed, so awaiting the flush here only delays the
            # next turn slightly and keeps the batch boundary deterministic.
            await self.flush()

    async def flush(self) -> None:
        """Ingest the buffered turns into long-term memory and clear the buffer.

        Best-effort: the reply is already sent, so a mem0 hiccup is logged and swallowed
        rather than surfaced. Called on the batch threshold and on shutdown. No-op when
        memory is off or the buffer is empty.
        """
        service = self._god.memory_service
        if service is None or not self._buffer:
            return
        events, self._buffer = self._buffer, []
        self._buffer_started = None
        try:
            await service.add_events_to_memory(
                app_name=constants.APP_NAME,
                user_id=self._user_id,
                events=events,
                session_id=self._session_id,
            )
        except Exception:
            logging.getLogger(constants.LOGGER_NAME).warning("auto-ingest to memory failed")


def build_handler(
    god: God, *, user_id: str = "god-user", session_id: str = "god-session"
) -> Handler:
    """Return a :data:`Handler` coroutine that runs ``text`` through God."""
    return GodHandler(god, user_id=user_id, session_id=session_id)
