"""Run a task on a soul — the shared core behind ``delegate_to_soul`` and the dispatcher.

``delegate_to_soul`` (an LLM tool, has a ``ToolContext``) and the missions dispatcher (a
daemon loop, has none) both need the same pipeline: decide *reuse-or-forge* via the
soul-smith, build the soul, run it in its sandboxed workspace through a nested ADK
``Runner``, and report the deliverable (summary + the files it wrote). This module is that
pipeline, split so each caller supplies the smith decision its own way:

* :func:`decide_soul` — runs the smith via a nested ``Runner`` (no ``ToolContext``); the
  dispatcher uses it.
* :func:`execute_decision` — the post-decision core (build + run + diff); both callers use it.

ADK is imported lazily inside the functions, per the heavy-deps convention.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from gaia import constants
from gaia.souls.smith import SoulDecision, build_soul_smith
from gaia.tools.fs.base import sandbox_for

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia

logger = logging.getLogger(__name__)

#: How long a soul may run before the run is abandoned (seconds).
SOUL_TIMEOUT = 300.0

#: Cap on the number of workspace files reported back.
MAX_FILES = 500


@dataclass
class SoulRun:
    """The outcome of running a task on a soul."""

    ok: bool
    soul_key: str
    soul_name: str
    created: bool
    reason: str = ""
    summary: str = ""
    workspace: str = ""
    files: list[str] = field(default_factory=list)
    error: str = ""


def existing_souls(gaia: Gaia) -> str:
    """Render the souls Gaia already knows as ``key: description`` lines (or 'none')."""
    lines = []
    for key in gaia.souls.list_keys():
        spec = gaia.souls.get(key)
        if spec is not None:
            lines.append(f"{spec.key}: {spec.description}")
    return "\n".join(lines) or "(none yet)"


def _snapshot(primary: Path) -> dict[str, float]:
    """Map each file in the workspace to its mtime, for before/after diffing."""
    return {
        str(p.relative_to(primary)): p.stat().st_mtime for p in primary.rglob("*") if p.is_file()
    }


def _changed(before: dict[str, float], after: dict[str, float]) -> list[str]:
    """Relative paths created or modified between two snapshots (sorted, capped).

    Keeps the workspace flat — a reused soul's old, unrelated deliverables stay put but are
    NOT reported again; only what this run touched comes back.
    """
    return sorted(rel for rel, mtime in after.items() if before.get(rel) != mtime)[:MAX_FILES]


async def run_soul_agent(gaia: Gaia, soul: Any, key: str, task: str, user_id: str) -> str:
    """Run ``soul`` on ``task`` in a fresh nested Runner; return its final text.

    The runner is given Gaia's ``memory_service`` and the caller's ``user_id`` so the soul's
    ``load_memory``/``remember`` tools read and write the *same* user's long-term memory.
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from gaia.core.plugins import ToolLoggingPlugin

    session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
    session_id = f"soul-{key}"
    await session_service.create_session(
        app_name=constants.APP_NAME, user_id=user_id, session_id=session_id
    )
    runner = Runner(
        app_name=constants.APP_NAME,
        agent=soul,
        session_service=session_service,
        memory_service=gaia.memory_service,
        plugins=[ToolLoggingPlugin()],
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


async def decide_soul(gaia: Gaia, task: str) -> SoulDecision:
    """Ask the soul-smith to reuse-or-forge a soul for ``task`` — no ``ToolContext`` needed.

    Runs the smith ``LlmAgent`` through its own nested ``Runner`` (the dispatcher path), then
    parses the structured :class:`SoulDecision` from the smith's final JSON response. The
    ``delegate_to_soul`` tool keeps its own ``AgentTool`` path (it has a live tool context).
    """
    import json

    model = gaia.config.llm.model or gaia.settings.model
    smith = build_soul_smith(model, gaia.config.llm.provider, gaia.config.llm.openai.use_oauth)
    request = f"TASK:\n{task}\n\nEXISTING SOULS:\n{existing_souls(gaia)}"
    raw = await run_soul_agent(gaia, smith, "smith", request, user_id="gaia")
    return SoulDecision.model_validate(json.loads(raw))


def resolve_spec(gaia: Gaia, decision: SoulDecision) -> tuple[Any, bool] | None:
    """Turn a smith decision into a concrete ``AgentSpec`` + ``created`` flag, or ``None``."""
    known = gaia.souls.list_keys()
    if decision.action == "reuse" and decision.soul_key in known:
        spec = gaia.souls.get(decision.soul_key)
        return (spec, False) if spec is not None else None
    if decision.spec is not None:
        return decision.spec, decision.spec.key not in known
    return None


async def execute_decision(gaia: Gaia, decision: SoulDecision, task: str, user_id: str) -> SoulRun:
    """Build the chosen soul and run it on ``task``; capture the workspace diff as artifacts.

    The post-decision core shared by ``delegate_to_soul`` and the dispatcher: persist/build
    the soul (``create_or_reuse``), snapshot its workspace, run it (bounded by
    :data:`SOUL_TIMEOUT`), and report the files it created/modified.
    """
    resolved = resolve_spec(gaia, decision)
    if resolved is None:
        return SoulRun(False, "", "", False, error="soul-smith returned no usable decision")
    spec, created = resolved

    soul = gaia.factory.create_or_reuse(spec)  # persist (new) + build the ADK agent
    primary = sandbox_for(constants.AGENTS_DIR, spec.key).primary
    before = _snapshot(primary)
    try:
        summary = await asyncio.wait_for(
            run_soul_agent(gaia, soul, spec.key, task, user_id), timeout=SOUL_TIMEOUT
        )
    except TimeoutError:
        return SoulRun(
            False, spec.key, spec.name, created, error=f"soul timed out after {SOUL_TIMEOUT:.0f}s"
        )
    except Exception as exc:
        return SoulRun(False, spec.key, spec.name, created, error=f"soul run failed: {exc}")

    files = _changed(before, _snapshot(primary))
    return SoulRun(
        ok=True,
        soul_key=spec.key,
        soul_name=spec.name,
        created=created,
        reason=decision.reason,
        summary=summary,
        workspace=str(primary),
        files=files,
    )
