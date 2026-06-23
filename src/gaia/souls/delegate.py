"""``delegate_to_soul`` — Gaia's tool to find-or-forge a specialist soul and run it.

This is the spawn/reuse loop. Gaia calls it for a complex task; the tool asks the
soul-smith to reuse an existing soul or forge a new one, builds it (with every tool),
runs it on the task inside the soul's own sandboxed workspace via a nested ADK ``Runner``,
and returns the deliverable (workspace path + the files it wrote).

It is attached only to the root agent (see :meth:`Gaia.build_root_agent`), never handed to
souls — so a soul can't spawn souls. ADK is imported lazily inside the closure.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from google.adk.tools.tool_context import ToolContext

from gaia.core.elicit import soul_elicitation_sink
from gaia.souls.run import SoulRun, execute_decision, existing_souls, soul_result
from gaia.souls.smith import SoulDecision, build_soul_smith

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia

#: Tool id, used as the ADK tool name (matches the closure name).
NAME = "delegate_to_soul"


def make_delegate(gaia: Gaia) -> Callable[..., Awaitable[dict[str, Any] | None]]:
    """Return the root-only ``delegate_to_soul`` tool bound to ``gaia``.

    Wrapped as a ``LongRunningFunctionTool`` where it's attached to the root agent: when the
    delegated soul calls ``ask_user``, this returns ``None`` to pause the root (the handler then
    surfaces the soul's question); a normal completion returns its result dict as usual.
    """

    async def delegate_to_soul(
        task: str,
        project: str = "",
        attachments: list[str] | None = None,
        *,
        tool_context: ToolContext,
    ) -> dict[str, Any] | None:
        """Hand a complex or specialized task to a specialist soul (found or newly
        forged); it writes its deliverables as files in its workspace. For build/creation
        tasks (e.g. "design a website"), not things you answer yourself.

        Media the soul made returns in ``media`` and is auto-sent to the user — don't
        re-read/re-serve/re-screenshot to show it.

        Args:
            task: the full task, in plain language.
            project: a short slug (e.g. "plant-shop"); reuse to keep editing, new/omit to
                start fresh. ``workspace`` is its dir.
            attachments: absolute paths of files to hand the soul (e.g. another soul's
                ``media``/``files``) — copied into its workspace as relative files.
        """

        model = gaia.config.llm.model or gaia.settings.model
        provider = gaia.config.llm.provider
        use_oauth = gaia.config.llm.openai.use_oauth
        try:
            decision = await _decide(
                model, provider, use_oauth, task, existing_souls(gaia), tool_context
            )
        except Exception as exc:
            return {"status": "error", "error_message": f"soul-smith failed: {exc}"}

        # ADK's public ToolContext exposes user_id; the dispatcher path passes None.
        user_id = getattr(tool_context, "user_id", None) or "gaia"
        run: SoulRun = await execute_decision(
            gaia, decision, task, user_id, project=project, attachments=attachments
        )
        if run.pending is not None:
            # The soul asked the user mid-task. Hand the pause up to the handler via the per-turn
            # sink (a contextvar-carried list — robust to tool_context.user_id mismatches), pin its
            # warm session so the reaper can't drop it, and return None so this long-running tool
            # pauses the root run.
            sink = soul_elicitation_sink.get()
            if sink is not None:
                sink.append(run.pending)
            gaia.soul_sessions.pin(run.pending.warm_key)
            return None
        return soul_result(run)

    return delegate_to_soul


async def _decide(
    model: str, provider: str, use_oauth: bool, task: str, existing: str, tool_context: ToolContext
) -> SoulDecision:
    """Run the soul-smith via ADK ``AgentTool`` and return its parsed decision.

    The tool path: the smith runs as an ``AgentTool`` inside the live turn's ``tool_context``.
    The dispatcher (no tool context) uses :func:`gaia.souls.run.decide_soul` instead.
    """
    from google.adk.tools.agent_tool import AgentTool

    smith = AgentTool(build_soul_smith(model, provider, use_oauth))
    request = f"TASK:\n{task}\n\nEXISTING SOULS:\n{existing}"
    raw = await smith.run_async(args={"request": request}, tool_context=tool_context)
    return raw if isinstance(raw, SoulDecision) else SoulDecision.model_validate(raw)
