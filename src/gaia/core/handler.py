"""Bridge a plain-text message to Gaia's ADK root agent and back to text.

Connectors speak :data:`~gaia.connectors.base.Handler`; ADK speaks ``Runner``
events over a session. :class:`GaiaHandler` is the thin glue between them. The ADK
imports are deferred so importing gaia stays cheap and the connectors remain
unit-testable without a model backend.
"""

from __future__ import annotations

import asyncio
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
        # The in-flight background ingest, if any. Threshold flushes run off the turn's
        # critical path so mem0's extraction LLM call never delays the next reply.
        self._flush_task: asyncio.Task[None] | None = None

    async def _ensure_runner(self) -> Any:
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        from gaia.core.plugins import ToolLoggingPlugin, ToolPermissionPlugin

        if self._runner is None:
            session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
            await session_service.create_session(
                app_name=constants.APP_NAME, user_id=self._user_id, session_id=self._session_id
            )
            self._runner = Runner(
                app_name=constants.APP_NAME,
                agent=self._gaia.build_root_agent(self),
                session_service=session_service,
                memory_service=self._gaia.memory_service,
                plugins=[ToolPermissionPlugin(self._gaia), ToolLoggingPlugin()],
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
        texts: list[str] = []
        try:
            async for event in runner.run_async(
                user_id=self._user_id, session_id=self._session_id, new_message=content
            ):
                turn_events.append(event)
                # Collect the final answer's text parts; they're emitted after the loop so
                # a screenshot taken this turn can carry the reply as its caption (one
                # message) rather than arriving as a separate "screenshot" image + text.
                if event.is_final_response() and event.content and event.content.parts:
                    texts.extend(part.text for part in event.content.parts if part.text)
        except Exception as exc:
            # A model error (rate limit, outage) or tool fault must not surface as a raw
            # traceback to the user. Log the detail, send a short apology, end the turn.
            logging.getLogger(constants.LOGGER_NAME).exception("gaia turn failed")
            log_event("turn_error", user=self._user_id, error=type(exc).__name__)
            await send(_friendly_error(exc))
            return

        await self._emit_reply(turn_events, texts, send)
        await self._buffer_turn(turn_events)

    async def _emit_reply(self, events: list[Any], texts: list[str], send: Send) -> None:
        """Send the turn's reply: an image (with the text as its caption) when a screenshot
        was taken, otherwise the text parts.

        Connectors that support media (WhatsApp) render the image with the caption as one
        message; text-only connectors degrade the Media to its caption (see ``as_text``),
        so either way the user gets the words attached to the picture, not a bare path.
        """
        from gaia.connectors.base import Media
        from gaia.core.screenshots import media_for_screenshots

        media = media_for_screenshots(events)
        if media:
            # The reply text becomes the first image's caption — one combined message.
            # Extra screenshots (rare) follow without a caption.
            caption = "\n".join(t.strip() for t in texts if t.strip())
            first = media[0]
            log_event("media_out", user=self._user_id, tool="screenshot", chars=len(caption))
            await send(Media(first.path, caption=caption or first.caption))
            for extra in media[1:]:
                log_event("media_out", user=self._user_id, tool="screenshot")
                await send(Media(extra.path, caption=""))
            return

        # No media: stream each text part as its own reply (one inbound can fan out to many).
        for text in texts:
            log_event("message_out", user=self._user_id, chars=len(text))
            await send(text)

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
            # Drain in the background: mem0's extraction LLM call must not sit on the
            # critical path between this reply and the next inbound turn (one handler
            # serves one conversation, so an awaited flush would delay the next message).
            self._schedule_flush()

    def _schedule_flush(self) -> None:
        """Kick off a background ingest, unless one is already draining the buffer."""
        if self._flush_task is not None and not self._flush_task.done():
            return
        self._flush_task = asyncio.create_task(self._drain())

    async def flush(self) -> None:
        """Ingest the buffered turns into long-term memory and clear the buffer.

        Blocks until memory is durable — called on shutdown and ``/reset`` where the
        caller wants the buffer drained before proceeding. Awaits any in-flight
        background ingest first so nothing is lost.
        """
        if self._flush_task is not None and not self._flush_task.done():
            await self._flush_task
        await self._drain()

    async def _drain(self) -> None:
        """Send the buffered turns to long-term memory and clear the buffer.

        Best-effort: the reply is already sent, so a mem0 hiccup is logged and swallowed
        rather than surfaced. No-op when memory is off or the buffer is empty.
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
