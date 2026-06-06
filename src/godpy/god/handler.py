"""Bridge a plain-text message to God's ADK root agent and back to text.

Connectors speak ``Handler = Callable[[str], Awaitable[str]]``; ADK speaks
``Runner`` events over a session. This module is the thin glue between them. The
ADK imports are deferred so importing godpy stays cheap and the connectors remain
unit-testable without a model backend.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from godpy.god.agent import God

Handler = Callable[[str], Awaitable[str]]

_APP_NAME = "godpy"


def build_handler(
    god: God, *, user_id: str = "god-user", session_id: str = "god-session"
) -> Handler:
    """Return a coroutine that runs ``text`` through God and yields the reply text.

    The ADK ``Runner`` and its session are built lazily on the first call and then
    reused, so conversation state persists across messages within a process.
    """
    state: dict[str, Any] = {}

    async def handle(text: str) -> str:
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types

        if "runner" not in state:
            session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
            await session_service.create_session(
                app_name=_APP_NAME, user_id=user_id, session_id=session_id
            )
            state["runner"] = Runner(
                app_name=_APP_NAME,
                agent=god.build_root_agent(),
                session_service=session_service,
            )

        runner = state["runner"]
        content = types.Content(role="user", parts=[types.Part(text=text)])

        reply = ""
        async for event in runner.run_async(
            user_id=user_id, session_id=session_id, new_message=content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                reply = event.content.parts[0].text or ""
        return reply

    return handle
