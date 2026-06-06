"""Bridge a plain-text message to God's ADK root agent and back to text.

Connectors speak :data:`~godpy.connectors.base.Handler`; ADK speaks ``Runner``
events over a session. :class:`GodHandler` is the thin glue between them. The ADK
imports are deferred so importing godpy stays cheap and the connectors remain
unit-testable without a model backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from godpy.connectors.base import Handler, Send

if TYPE_CHECKING:  # pragma: no cover - typing only
    from godpy.god.agent import God

_APP_NAME = "godpy"


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
                app_name=_APP_NAME, user_id=self._user_id, session_id=self._session_id
            )
            self._runner = Runner(
                app_name=_APP_NAME,
                agent=self._god.build_root_agent(),
                session_service=session_service,
            )
        return self._runner

    async def __call__(self, text: str, send: Send) -> None:
        from google.genai import types

        runner = await self._ensure_runner()
        content = types.Content(role="user", parts=[types.Part(text=text)])

        async for event in runner.run_async(
            user_id=self._user_id, session_id=self._session_id, new_message=content
        ):
            # A model turn can carry several parts (text, function calls, inline
            # data). Stream each text part of the final answer as its own reply
            # instead of joining them, so one inbound message can fan out to many.
            if event.is_final_response() and event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        await send(part.text)


def build_handler(
    god: God, *, user_id: str = "god-user", session_id: str = "god-session"
) -> Handler:
    """Return a :data:`Handler` coroutine that runs ``text`` through God."""
    return GodHandler(god, user_id=user_id, session_id=session_id)
