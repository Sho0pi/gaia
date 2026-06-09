"""Bridge a plain-text message to God's ADK root agent and back to text.

Connectors speak :data:`~godpy.connectors.base.Handler`; ADK speaks ``Runner``
events over a session. :class:`GodHandler` is the thin glue between them. The ADK
imports are deferred so importing godpy stays cheap and the connectors remain
unit-testable without a model backend.
"""

from __future__ import annotations

import logging
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

    async def _ensure_runner(self) -> Any:
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

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

        await self._ingest(turn_events)

    async def _ingest(self, events: list[Any]) -> None:
        """Auto-feed this turn to long-term memory so mem0 extracts durable facts.

        Best-effort: the reply has already been sent, so a mem0 hiccup is logged and
        swallowed rather than surfaced to the user. No-op when memory is off or
        ``memory.auto_ingest`` is false.
        """
        service = self._god.memory_service
        if service is None or not self._god.config.memory.auto_ingest:
            return
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
