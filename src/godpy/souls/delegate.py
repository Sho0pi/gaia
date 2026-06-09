"""``delegate_to_soul`` — God's tool to find-or-forge a specialist soul and run it.

This is the spawn/reuse loop. God calls it for a complex task; the tool asks the
soul-smith to reuse an existing soul or forge a new one, builds it (with every tool),
runs it on the task inside the soul's own sandboxed workspace via a nested ADK ``Runner``,
and returns the deliverable (workspace path + the files it wrote).

It is attached only to the root agent (see :meth:`God.build_root_agent`), never handed to
souls — so a soul can't spawn souls. ADK is imported lazily inside the closure.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from google.adk.tools.tool_context import ToolContext

from godpy import constants
from godpy.logs import log_event
from godpy.souls.smith import SoulDecision, build_soul_smith
from godpy.tools.fs.base import sandbox_for

if TYPE_CHECKING:  # pragma: no cover - typing only
    from godpy.god.agent import God

#: Tool id, used as the ADK tool name (matches the closure name).
NAME = "delegate_to_soul"

#: How long a soul may run before the delegation is abandoned (seconds).
SOUL_TIMEOUT = 300.0

#: Cap on the number of workspace files reported back.
MAX_FILES = 500


def _existing_souls(god: God) -> str:
    """Render the souls God already knows as ``key: description`` lines (or 'none')."""
    lines = []
    for key in god.registry.list_keys():
        spec = god.registry.get(key)
        if spec is not None:
            lines.append(f"{spec.key}: {spec.description}")
    return "\n".join(lines) or "(none yet)"


def _workspace_files(key: str) -> tuple[str, list[str]]:
    """Return the soul's workspace path and the relative paths of files it contains."""
    primary = sandbox_for(constants.AGENTS_DIR, key).primary
    files = sorted(str(p.relative_to(primary)) for p in primary.rglob("*") if p.is_file())
    return str(primary), files[:MAX_FILES]


def make_delegate(god: God) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the root-only ``delegate_to_soul`` tool bound to ``god``."""

    async def delegate_to_soul(task: str, *, tool_context: ToolContext) -> dict[str, Any]:
        """Hand a complex or specialized task to a specialist soul.

        Finds the soul best suited to the task, or forges a new one if none fits, then runs
        it — the soul writes its deliverables as files in its own workspace. Use this for
        build/creation tasks (e.g. "design a website"), not for things you can answer yourself.

        Args:
            task (str): The full task to carry out, in plain language.

        Returns:
            dict: On success {'status': 'success', 'soul': str, 'created': bool, 'reason': str,
            'workspace': str, 'files': list[str], 'summary': str}. On failure {'status':
            'error', 'error_message': str}.
        """

        def done(result: dict[str, Any]) -> dict[str, Any]:
            log_event(
                "tool_used",
                tool=NAME,
                soul=result.get("soul"),
                forged=result.get("created"),  # 'created' is a reserved LogRecord field
                status=result["status"],
            )
            return result

        model = god.config.llm.model or god.settings.model
        try:
            decision = await _decide(model, task, _existing_souls(god), tool_context)
        except Exception as exc:
            return done({"status": "error", "error_message": f"soul-smith failed: {exc}"})

        known = god.registry.list_keys()
        if decision.action == "reuse" and decision.soul_key in known:
            spec = god.registry.get(decision.soul_key)
            created = False
        elif decision.spec is not None:
            spec = decision.spec
            created = spec.key not in known
        else:
            return done(
                {"status": "error", "error_message": "soul-smith returned no usable decision"}
            )
        assert spec is not None  # narrowed by the branches above

        soul = god.factory.create_or_reuse(spec)  # persist (new) + build the ADK agent

        try:
            summary = await asyncio.wait_for(_run_soul(soul, spec.key, task), timeout=SOUL_TIMEOUT)
        except TimeoutError:
            return done(
                {
                    "status": "error",
                    "soul": spec.key,
                    "created": created,
                    "error_message": f"soul timed out after {SOUL_TIMEOUT:.0f}s",
                }
            )
        except Exception as exc:
            return done(
                {
                    "status": "error",
                    "soul": spec.key,
                    "created": created,
                    "error_message": f"soul run failed: {exc}",
                }
            )

        workspace, files = _workspace_files(spec.key)
        return done(
            {
                "status": "success",
                "soul": spec.name,
                "created": created,
                "reason": decision.reason,
                "workspace": workspace,
                "files": files,
                "summary": summary,
            }
        )

    return delegate_to_soul


async def _decide(model: str, task: str, existing: str, tool_context: ToolContext) -> SoulDecision:
    """Run the soul-smith via ADK ``AgentTool`` and return its parsed decision."""
    from google.adk.tools.agent_tool import AgentTool

    smith = AgentTool(build_soul_smith(model))
    request = f"TASK:\n{task}\n\nEXISTING SOULS:\n{existing}"
    raw = await smith.run_async(args={"request": request}, tool_context=tool_context)
    return raw if isinstance(raw, SoulDecision) else SoulDecision.model_validate(raw)


async def _run_soul(soul: Any, key: str, task: str) -> str:
    """Run the soul on ``task`` in a fresh nested Runner; return its final text."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from godpy.god.plugins import ToolLoggingPlugin
    from godpy.tools import SELF_LOGGING_TOOLS

    session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
    user_id, session_id = "god", f"soul-{key}"
    await session_service.create_session(
        app_name=constants.APP_NAME, user_id=user_id, session_id=session_id
    )
    runner = Runner(
        app_name=constants.APP_NAME,
        agent=soul,
        session_service=session_service,
        plugins=[ToolLoggingPlugin(SELF_LOGGING_TOOLS)],
    )
    content = types.Content(role="user", parts=[types.Part(text=task)])
    parts: list[str] = []
    try:
        async for event in runner.run_async(
            user_id=user_id, session_id=session_id, new_message=content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                parts.extend(p.text for p in event.content.parts if p.text)
    finally:
        await runner.close()  # type: ignore[no-untyped-call]
    return "\n".join(parts)
