"""Bridge a plain-text message to Gaia's ADK root agent and back to text.

Connectors speak :data:`~gaia.connectors.base.Handler`; ADK speaks ``Runner``
events over a session. :class:`GaiaHandler` is the thin glue between them. The ADK
imports are deferred so importing gaia stays cheap and the connectors remain
unit-testable without a model backend.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from gaia import constants
from gaia.connectors.base import Send
from gaia.logs import log_event

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia


def _friendly_error(exc: Exception) -> str:
    """A short, user-facing message for a failed turn (rate limit / outage / other)."""
    text = str(exc)
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        return "I'm being rate-limited right now (model quota). Please try again in a minute."
    if "503" in text or "UNAVAILABLE" in text or "overloaded" in text.lower():
        return "The model is busy at the moment. Please try again shortly."
    return "Sorry — something went wrong handling that. Please try again."


class GaiaHandler:
    """Runs inbound text through Gaia's ADK root agent and returns the reply text.

    The ADK ``Runner`` and its session are expensive to build and hold the running
    conversation, so they're created once on the first message and kept on the
    instance (``self._runner``); later messages reuse them, which is what gives the
    bot memory within a process. One ``GaiaHandler`` == one conversation.
    """

    def __init__(
        self,
        gaia: Gaia,
        *,
        user_id: str = "gaia-user",
        session_id: str = "gaia-session",
        role: str = "admin",
    ) -> None:
        self._gaia = gaia
        self._user_id = user_id
        self._session_id = session_id
        # The caller's role; commands gate on it (e.g. admin-only /approve). Defaults to
        # admin so single-user / cron / test callers that don't resolve a user are trusted.
        self._role = role
        self._runner: Any | None = None
        # Auto-ingest buffer: turns accumulate here and flush in batches (by count or
        # age) so mem0's per-add extraction LLM call fires once per batch, not per turn.
        self._buffer: list[Any] = []
        self._buffer_started: float | None = None

    async def _ensure_runner(self) -> Any:
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        from gaia.core.plugins import ToolLoggingPlugin

        if self._runner is None:
            session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
            await session_service.create_session(
                app_name=constants.APP_NAME, user_id=self._user_id, session_id=self._session_id
            )
            self._runner = Runner(
                app_name=constants.APP_NAME,
                agent=self._gaia.build_root_agent(),
                session_service=session_service,
                memory_service=self._gaia.memory_service,
                plugins=[ToolLoggingPlugin()],
            )
        return self._runner

    async def __call__(self, text: str, send: Send) -> None:
        from google.genai import types

        log_event("message_in", user=self._user_id, session=self._session_id, chars=len(text))

        # A slash command is handled out-of-band: it never reaches the model or the
        # memory ingest path.
        if await self._maybe_run_command(text, send):
            return

        runner = await self._ensure_runner()
        content = types.Content(role="user", parts=[types.Part(text=text)])

        turn_events: list[Any] = []
        try:
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
        except Exception as exc:
            # A model error (rate limit, outage) or tool fault must not surface as a raw
            # traceback to the user. Log the detail, send a short apology, end the turn.
            logging.getLogger(constants.LOGGER_NAME).exception("gaia turn failed")
            log_event("turn_error", user=self._user_id, error=type(exc).__name__)
            await send(_friendly_error(exc))
            return

        await self._emit_screenshots(turn_events, send)
        await self._buffer_turn(turn_events)

    async def _emit_screenshots(self, events: list[Any], send: Send) -> None:
        """Deliver any screenshots taken this turn as media replies (see core.screenshots)."""
        from gaia.core.screenshots import media_for_screenshots

        for media in media_for_screenshots(events):
            log_event("media_out", user=self._user_id, tool="screenshot")
            await send(media)

    def reset_session(self) -> None:
        """Drop the live ADK session and pending memory buffer (used by ``/reset``).

        Nulling ``_runner`` makes the next message build a fresh session with no prior
        turns; long-term memory is untouched.
        """
        self._runner = None
        self._buffer = []
        self._buffer_started = None

    async def _maybe_run_command(self, text: str, send: Send) -> bool:
        """If ``text`` is a slash command, run it and reply; return whether it was one."""
        from gaia.commands import CommandContext, default_registry, parse

        parsed = parse(text)
        if parsed is None:
            return False
        name, args = parsed

        registry = default_registry(self._gaia.config)
        command = registry.get(name)
        if command is None:
            log_event("command_used", command=name, status="unknown")
            await send(f"Unknown command '/{name}'. Try /help.")
            return True

        ctx = CommandContext(
            args=args,
            gaia=self._gaia,
            handler=self,
            registry=registry,
            user_id=self._user_id,
            session_id=self._session_id,
            role=self._role,
        )
        reply = await command.run(ctx)
        log_event("command_used", command=command.name, status="ok")
        await send(reply)
        return True

    async def _buffer_turn(self, events: list[Any]) -> None:
        """Add a turn to the auto-ingest buffer, flushing when it's full or stale."""
        service = self._gaia.memory_service
        if service is None or not self._gaia.config.memory.auto_ingest:
            return
        if not events:
            return
        if self._buffer_started is None:
            self._buffer_started = time.monotonic()
        self._buffer.extend(events)

        memory = self._gaia.config.memory
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
        service = self._gaia.memory_service
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
    gaia: Gaia,
    *,
    user_id: str = "gaia-user",
    session_id: str = "gaia-session",
    role: str = "admin",
) -> GaiaHandler:
    """Return a :class:`GaiaHandler` that runs ``text`` through Gaia as ``user_id``."""
    return GaiaHandler(gaia, user_id=user_id, session_id=session_id, role=role)
