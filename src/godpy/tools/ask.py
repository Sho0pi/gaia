"""The ``ask`` tool: pause and ask the user a clarifying question, with optional choices.

Unlike every other tool, ``ask`` does not produce its answer itself — the answer
arrives out of band, on the user's *next* message. So this is registered as an ADK
:class:`~google.adk.tools.long_running_tool.LongRunningFunctionTool`
(see :func:`godpy.tools.registry.default_registry`): the closure returns a ``pending``
ticket, ADK ends the model turn, and :class:`~godpy.god.handler.GodHandler` renders
the question to the active connector and later resumes the run with a
``FunctionResponse`` carrying the reply. The closure here only validates and emits the
ticket; it never blocks.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from godpy.logs import log_event

#: Tool id, used by the registry and as the ADK tool name (matches the closure name).
NAME = "ask"


def make_ask() -> Callable[..., dict[str, Any]]:
    """Return the ADK ``ask`` tool.

    ADK reads the returned function's name, signature and docstring to build the tool
    schema, so the closure's name matches :data:`NAME` and documents its args + return.
    """

    def ask(
        question: str,
        options: list[str] | None = None,
        option_descriptions: list[str] | None = None,
        multi_select: bool = False,
    ) -> dict[str, Any]:
        """Ask the user a clarifying question and pause until they answer.

        Use this when a decision is genuinely the user's to make and you cannot
        resolve it from context — not for choices with an obvious default. Execution
        pauses; the user's next message is delivered back as the answer.

        Args:
            question (str): The question to ask the user.
            options (list[str]): Optional suggested choices. Omit for a free-text
                answer. When given, the connector may render them as a picker, but the
                user can still answer with free text.
            option_descriptions (list[str]): Optional one-line gloss per option; when
                given it must be the same length as ``options``.
            multi_select (bool): Allow the user to pick more than one option.

        Returns:
            dict: On success, {'status': 'pending', 'ask_id': str, 'question': str,
            'options': list[str]} — a ticket the runtime resolves out of band. On bad
            input, {'status': 'error', 'error_message': str}.
        """
        cleaned = question.strip()
        opts = options or []

        def done(result: dict[str, Any]) -> dict[str, Any]:
            log_event(
                "tool_used",
                tool=NAME,
                status=result["status"],
                n_options=len(opts),
                multi_select=multi_select,
            )
            return result

        if not cleaned:
            return done({"status": "error", "error_message": "question must not be empty"})

        if option_descriptions is not None and len(option_descriptions) != len(opts):
            return done(
                {
                    "status": "error",
                    "error_message": (
                        "option_descriptions must be the same length as options "
                        f"({len(option_descriptions)} != {len(opts)})"
                    ),
                }
            )

        return done(
            {
                "status": "pending",
                "ask_id": uuid.uuid4().hex,
                "question": cleaned,
                "options": opts,
            }
        )

    return ask
