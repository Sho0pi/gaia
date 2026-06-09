"""Bridge a plain-text message to God's ADK root agent and back to text.

Connectors speak :data:`~godpy.connectors.base.Handler`; ADK speaks ``Runner``
events over a session. :class:`GodHandler` is the thin glue between them. The ADK
imports are deferred so importing godpy stays cheap and the connectors remain
unit-testable without a model backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from godpy import constants
from godpy.connectors.base import Ask, Handler, Send, render_ask_text
from godpy.logs import log_event
from godpy.tools.ask import NAME as ASK

if TYPE_CHECKING:  # pragma: no cover - typing only
    from godpy.god.agent import God


class GodHandler:
    """Runs inbound text through God's ADK root agent and returns the reply text.

    The ADK ``Runner`` and its session are expensive to build and hold the running
    conversation, so they're created once on the first message and kept on the
    instance (``self._runner``); later messages reuse them, which is what gives the
    bot memory within a process. One ``GodHandler`` == one conversation.

    The ``ask`` tool is long-running: when the agent asks a question the run pauses
    with the function call left dangling. The handler stashes that call in
    ``self._pending``, renders the question, and treats the *next* inbound message as
    the answer — resuming the run with a matching ``FunctionResponse`` instead of a
    fresh user turn. Only one ask can be outstanding at a time.
    """

    def __init__(
        self, god: God, *, user_id: str = "god-user", session_id: str = "god-session"
    ) -> None:
        self._god = god
        self._user_id = user_id
        self._session_id = session_id
        self._runner: Any | None = None
        self._pending: dict[str, str] | None = None

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
            )
        return self._runner

    async def __call__(self, text: str, send: Send) -> None:
        from google.genai import types

        log_event("message_in", user=self._user_id, session=self._session_id, chars=len(text))
        runner = await self._ensure_runner()

        if self._pending is not None:
            # The inbound text answers a dangling ``ask``. Resume that invocation with a
            # FunctionResponse matched by id (ADK infers the invocation from it) rather
            # than starting a new user turn.
            pending = self._pending
            self._pending = None
            log_event("ask_resumed", user=self._user_id, chars=len(text))
            content = types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=pending["call_id"],
                            name=pending["name"],
                            response={"status": "success", "answer": text},
                        )
                    )
                ],
            )
        else:
            content = types.Content(role="user", parts=[types.Part(text=text)])

        async for event in runner.run_async(
            user_id=self._user_id, session_id=self._session_id, new_message=content
        ):
            # An ``ask`` call surfaces as a long-running function call: pause here,
            # render the question, and wait for the next message (the answer).
            lr_ids = getattr(event, "long_running_tool_ids", None)
            if lr_ids:
                for call in event.get_function_calls():
                    if call.id in lr_ids and call.name == ASK:
                        self._pending = {"call_id": call.id, "name": call.name}
                        await self._render_question(call.args or {}, send)
                        return

            # A model turn can carry several parts (text, function calls, inline
            # data). Stream each text part of the final answer as its own reply
            # instead of joining them, so one inbound message can fan out to many.
            if event.is_final_response() and event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        log_event("message_out", user=self._user_id, chars=len(part.text))
                        await send(part.text)

    async def _render_question(self, args: dict[str, Any], send: Send) -> None:
        """Surface an ``ask`` question: native picker if the connector offers one, else text.

        Built from the model's call ``args`` (not the tool's return), so no tool-output
        plumbing is needed. A connector advertises native rendering by attaching an
        ``AskSend`` as ``send.ask``; otherwise we fall back to the plain-text floor.
        """
        ask = Ask(
            question=str(args.get("question", "")),
            options=list(args.get("options") or []),
            option_descriptions=args.get("option_descriptions"),
            multi_select=bool(args.get("multi_select", False)),
            ask_id=str(args.get("ask_id", "")),
        )
        log_event("ask_pending", user=self._user_id, n_options=len(ask.options))
        asker = getattr(send, "ask", None)
        if asker is not None:
            await asker(ask)
        else:
            await send(render_ask_text(ask))


def build_handler(
    god: God, *, user_id: str = "god-user", session_id: str = "god-session"
) -> Handler:
    """Return a :data:`Handler` coroutine that runs ``text`` through God."""
    return GodHandler(god, user_id=user_id, session_id=session_id)
