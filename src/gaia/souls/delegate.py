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

from gaia.souls.run import SoulRun, execute_decision, existing_souls
from gaia.souls.smith import SoulDecision, build_soul_smith

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia

#: Tool id, used as the ADK tool name (matches the closure name).
NAME = "delegate_to_soul"


def make_delegate(gaia: Gaia) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the root-only ``delegate_to_soul`` tool bound to ``gaia``."""

    async def delegate_to_soul(
        task: str, project: str = "", *, tool_context: ToolContext
    ) -> dict[str, Any]:
        """Hand a complex or specialized task to a specialist soul (found or newly
        forged); it writes its deliverables as files in its own workspace. Use for
        build/creation tasks (e.g. "design a website"), not things you answer yourself.

        Media the soul made (a screenshot, a generated image or PDF) returns in ``media``
        and is auto-sent to the user — do NOT re-read, re-serve, or re-screenshot to show it.

        Args:
            task: the full task to carry out, in plain language.
            project: a short slug for this project (e.g. "plant-shop"). Reuse a slug to keep
                editing it; use a new one (or omit) to start fresh — keeps projects from
                overwriting each other. ``workspace`` is the project's dir.
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
        run: SoulRun = await execute_decision(gaia, decision, task, user_id, project=project)
        if not run.ok:
            return {
                "status": "error",
                "soul": run.soul_key,
                "created": run.created,
                "error_message": run.error,
            }
        return {
            "status": "success",
            "soul": run.soul_name,
            "created": run.created,
            "reason": run.reason,
            "workspace": run.workspace,
            "project": run.project,
            "files": run.files,
            "media": run.media,
            "summary": run.summary,
        }

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
