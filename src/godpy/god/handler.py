"""Bridge a plain-text message to God's ADK root agent and back to text.

Connectors speak :data:`~godpy.connectors.base.Handler`; ADK speaks ``Runner``
events over a session. :class:`GodHandler` is the thin glue between them. The ADK
imports are deferred so importing godpy stays cheap and the connectors remain
unit-testable without a model backend.
"""

from __future__ import annotations

import base64
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from godpy import constants
from godpy.connectors.base import Handler, Send
from godpy.logs import log_event

if TYPE_CHECKING:  # pragma: no cover - typing only
    from godpy.connectors.base import Media
    from godpy.god.agent import God

#: playwright-mcp's screenshot tool (the mcp browser backend). Its result is an MCP
#: ``CallToolResult`` dict (content blocks), not the native tool's ``{"path": ...}``.
_MCP_SCREENSHOT = "browser_take_screenshot"
#: Matches a saved image path inside playwright-mcp's text response.
_IMAGE_PATH_RE = re.compile(r"\S+\.(?:png|jpe?g)", re.IGNORECASE)


def _friendly_error(exc: Exception) -> str:
    """A short, user-facing message for a failed turn (rate limit / outage / other)."""
    text = str(exc)
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        return "I'm being rate-limited right now (model quota). Please try again in a minute."
    if "503" in text or "UNAVAILABLE" in text or "overloaded" in text.lower():
        return "The model is busy at the moment. Please try again shortly."
    return "Sorry — something went wrong handling that. Please try again."


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
            logging.getLogger(constants.LOGGER_NAME).exception("god turn failed")
            log_event("turn_error", user=self._user_id, error=type(exc).__name__)
            await send(_friendly_error(exc))
            return

        await self._emit_screenshots(turn_events, send)
        await self._buffer_turn(turn_events)

    async def _emit_screenshots(self, events: list[Any], send: Send) -> None:
        """Deliver any screenshots taken this turn as media replies.

        The model only streams text; a screenshot tool writes a PNG and reports it in
        the tool result. We scan this turn's tool responses for those files and push
        each through ``send`` as a :class:`Media` reply, so a connector that supports
        images (WhatsApp) delivers the actual picture instead of just a path. Both
        backends are handled: the native ``browser_screenshot`` (``{"path": ...}``) and
        playwright-mcp's ``browser_take_screenshot`` (an MCP content-block dict). Only
        screenshots God itself takes are seen here — files a delegated soul produces
        come back via delegate_to_soul and are a follow-up.
        """
        for event in events:
            get_responses = getattr(event, "get_function_responses", None)
            if get_responses is None:
                continue
            for resp in get_responses() or []:
                media = _screenshot_media(resp.name, resp.response)
                if media is not None:
                    log_event("media_out", user=self._user_id, tool=resp.name)
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
        from godpy.commands import CommandContext, default_registry, parse

        parsed = parse(text)
        if parsed is None:
            return False
        name, args = parsed

        registry = default_registry(self._god.config)
        command = registry.get(name)
        if command is None:
            log_event("command_used", command=name, status="unknown")
            await send(f"Unknown command '/{name}'. Try /help.")
            return True

        ctx = CommandContext(
            args=args,
            god=self._god,
            handler=self,
            registry=registry,
            user_id=self._user_id,
            session_id=self._session_id,
        )
        reply = await command.run(ctx)
        log_event("command_used", command=command.name, status="ok")
        await send(reply)
        return True

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


def _screenshot_media(name: str, result: Any) -> Media | None:
    """A :class:`Media` reply for a screenshot tool result, or ``None`` if it isn't one.

    Handles both browser backends: the native ``browser_screenshot`` (returns a
    ``{"status": "success", "path": ...}`` dict) and playwright-mcp's
    ``browser_take_screenshot`` (returns an MCP ``CallToolResult`` dict of content
    blocks — a text block naming the saved file and/or an inline base64 image).
    """
    from godpy.connectors.base import Media
    from godpy.tools.browser import SCREENSHOT

    if not isinstance(result, dict):
        return None
    if name == SCREENSHOT and result.get("status") == "success" and result.get("path"):
        return Media(Path(result["path"]), caption="screenshot")
    if name == _MCP_SCREENSHOT and not result.get("isError"):
        path = _mcp_screenshot_path(result)
        if path is not None:
            return Media(path, caption="screenshot")
    return None


def _mcp_screenshot_path(result: dict[str, Any]) -> Path | None:
    """Extract the saved image file from a playwright-mcp screenshot result.

    Prefers a real file path named in a text block (playwright-mcp saves into the
    ``--output-dir`` we pin); falls back to decoding an inline base64 image block into
    the browser workspace so we still deliver the picture if no path is reported.
    """
    content = result.get("content")
    if not isinstance(content, list):
        return None
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            for token in _IMAGE_PATH_RE.findall(str(item.get("text", ""))):
                candidate = Path(token.strip("'\"`.,"))
                if candidate.is_file():
                    return candidate
    for item in content:
        if isinstance(item, dict) and item.get("type") == "image" and item.get("data"):
            from godpy.mcp import browser_output_dir

            try:
                blob = base64.b64decode(item["data"])
            except (ValueError, TypeError):
                continue
            out = browser_output_dir()
            out.mkdir(parents=True, exist_ok=True)
            target = out / f"screenshot-{int(time.time() * 1000)}.png"
            target.write_bytes(blob)
            return target
    return None


def build_handler(
    god: God, *, user_id: str = "god-user", session_id: str = "god-session"
) -> Handler:
    """Return a :data:`Handler` coroutine that runs ``text`` through God."""
    return GodHandler(god, user_id=user_id, session_id=session_id)
