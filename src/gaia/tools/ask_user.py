"""The ``ask_user`` tool: pause the run to ask the human, resume with their answer.

Unlike every other gaia tool, ``ask_user`` returns nothing and *pauses* the turn. It
is an ADK ``LongRunningFunctionTool``: returning ``None`` makes ADK emit the function
call with no response, so ``run_async`` completes and ``GaiaHandler`` can surface the
question over the connector. The user's reply is fed back as this call's
``FunctionResponse`` to resume the *same* run (see ``gaia.core.handler`` and
``gaia.core.elicit``). The question/options/secret travel in the call's arguments,
which the handler reads — the function body only turns off result-summarization (so the
model continues straight from the answer) and yields control.
"""

from __future__ import annotations

from google.adk.tools.long_running_tool import LongRunningFunctionTool
from google.adk.tools.tool_context import ToolContext

#: Tool id; also the ADK tool name (matches the closure name) the handler matches on.
NAME = "ask_user"


def make_ask_user() -> LongRunningFunctionTool:
    """Return the ADK ``ask_user`` long-running tool (pauses the run for a human reply)."""

    def ask_user(
        question: str,
        options: list[str] | None = None,
        secret: bool = False,
        multi: bool = False,
        *,
        tool_context: ToolContext,
    ) -> None:
        """Ask the user a question and wait for their reply before continuing. Use when
        you genuinely cannot proceed without input only the user can give — a choice, a
        missing detail, or a credential — instead of guessing or giving up.

        Args:
            question: the question to show the user.
            options: choices to offer for a multiple-choice answer; omit for free text.
            secret: true if the answer is sensitive (e.g. an API key) so it is kept out
                of long-term memory and logs.
            multi: true to let the user pick more than one of ``options`` (the answer
                comes back as the chosen labels joined by ", "); ignored without options.
        """
        tool_context.actions.skip_summarization = True
        return None

    return LongRunningFunctionTool(func=ask_user)
