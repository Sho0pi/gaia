"""``consult_soul`` — a soul's synchronous "ask an expert" tool.

The second delegation path (the first is the board's ``task_create``). Where a subtask is
async work-product delegation, ``consult_soul`` is a *quick question* answered **inside the
caller's turn**: the ADK function-call loop is the resume, so the expert's answer lands
right back in the asking soul's context — no board hop, no wait.

It runs the same find-or-forge smith path as the dispatcher, then runs the chosen soul
synchronously and returns its text. Recursion is bounded three ways: a depth cap and a
cycle guard (both carried in the soul's session ``state``) plus the soul run timeout.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from google.adk.tools.tool_context import ToolContext  # runtime: ADK reads it for the schema

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia

#: Tool id / ADK tool name (matches the closure name).
NAME = "consult_soul"


def make_consult_soul(gaia: Gaia) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the ``consult_soul`` tool bound to ``gaia`` (handed to souls, not the root)."""

    async def consult_soul(question: str, *, tool_context: ToolContext) -> dict[str, Any]:
        """Ask another specialist a quick question and get the answer back in this turn.

        Use this when you need an *answer* (a fact, an opinion, a calculation) to keep
        working — not when you need a *deliverable* produced (file a subtask with
        task_create for that). The answer returns to you immediately.

        Args:
            question: the question to ask, with enough context to answer it standalone.
        """
        from gaia.souls.run import decide_soul, execute_decision

        if not question.strip():
            return {"status": "error", "error_message": "question must not be empty"}
        raw = getattr(tool_context, "state", None)
        if raw is None:
            state = {}
        elif hasattr(raw, "to_dict"):  # ADK's State object (not a plain dict)
            state = dict(raw.to_dict())
        else:
            state = dict(raw)
        depth = int(state.get("consult_depth", 0))
        cap = gaia.config.missions.consult_depth
        if depth >= cap:
            return {"status": "error", "error_message": f"consult depth limit reached ({cap})"}
        chain = list(state.get("consult_chain", []))
        user_id = getattr(tool_context, "user_id", None) or "gaia"

        decision = await decide_soul(gaia, question)
        key = decision.soul_key or (decision.spec.key if decision.spec else "")
        if key and key in chain:  # A→B→A: that expert is already on the consult stack
            return {"status": "error", "error_message": f"consult cycle: {key} already consulting"}

        run_state = {
            "consult_depth": depth + 1,
            "consult_chain": [*chain, key] if key else chain,
            "owner": user_id,
        }
        run = await execute_decision(gaia, decision, question, user_id, state=run_state)
        if not run.ok:
            return {"status": "error", "error_message": run.error or "consult failed"}
        return {"status": "success", "soul": run.soul_name, "answer": run.summary}

    return consult_soul
