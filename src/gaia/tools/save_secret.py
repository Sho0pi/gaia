"""The ``save_secret`` tool: collect a secret from the user into ``~/.gaia/.env``, off the model.

Like :mod:`gaia.tools.ask_user` it is an ADK ``LongRunningFunctionTool`` that *pauses* the run:
returning ``None`` makes ADK emit the call with no response, so ``GaiaHandler`` can prompt the user.
The crucial difference is on resume — the handler writes the pasted value into ``.env`` (and the
live env) and feeds the model back only a "saved" confirmation, never the value. So a secret (an
API key/token) can be collected in chat without ever passing through the model or the logs.
Admin-gated (ACL ``manage_users``), since it writes the operator's secrets file.
"""

from __future__ import annotations

from google.adk.tools.long_running_tool import LongRunningFunctionTool
from google.adk.tools.tool_context import ToolContext

#: Tool id; also the ADK tool name (matches the closure name) the handler matches on.
NAME = "save_secret"


def make_save_secret() -> LongRunningFunctionTool:
    """Return the ADK ``save_secret`` long-running tool (pauses for a secret, stores it in .env)."""

    def save_secret(env_var: str, reason: str = "", *, tool_context: ToolContext) -> None:
        """Securely collect a secret (API key / token) from the user and store it in ~/.gaia/.env.

        Use this instead of asking for a secret in plain chat: what the user pastes goes straight
        into their .env and the running environment — it never passes through you or the logs, so
        you won't see the value (you get back only a "saved" confirmation). Pair it with manage_mcp
        for a token-authenticated server: call save_secret(env_var="TICKTICK_TOKEN"), then add the
        server with headers={"Authorization": "Bearer ${TICKTICK_TOKEN}"} (or, for a stdio server,
        env_passthrough=["TICKTICK_TOKEN"]). Admin only.

        Args:
            env_var: the env variable NAME to store the secret under (e.g. "TICKTICK_TOKEN").
            reason: a short note shown to the user about what the secret is for.
        """
        # Same idiom as ask_user: no result to narrate — returning None pauses the run, and the
        # handler does the real work (write to .env) on resume. skip_summarization keeps ADK from
        # spending a model call narrating the (empty) tool output.
        tool_context.actions.skip_summarization = True
        return None

    return LongRunningFunctionTool(func=save_secret)
