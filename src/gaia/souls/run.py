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
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from gaia import constants
from gaia.connectors.base import inbound_attachments, media_kind
from gaia.souls.smith import SoulDecision, build_soul_smith
from gaia.tools.fs.base import _safe_dir, current_agent, current_project, is_denied, sandbox_for

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia

logger = logging.getLogger(__name__)

#: How long a soul may run before the run is abandoned (seconds).
SOUL_TIMEOUT = 300.0

#: Cap on the number of workspace files reported back.
MAX_FILES = 500

#: Workspace file types auto-delivered to the user as a soul's deliverable, on top of the real
#: image/video/audio that ``media_kind`` already flags. These are the "document" artifacts worth
#: sending as a file (a report, a sheet, a bundle); the web-source a site is built from
#: (``.html``/``.css``/``.js``/``.json``…) is deliberately excluded so we send the screenshot
#: preview, not the source. Add a suffix here as new deliverable kinds come up.
DELIVERABLE_DOC_SUFFIXES = frozenset(
    {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".csv", ".zip", ".epub"}
)


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
    project: str = ""
    files: list[str] = field(default_factory=list)
    #: Absolute paths of media the soul produced for the user (screenshots it took, files it
    #: sent, and media deliverables written to its workspace) — auto-delivered by the handler
    #: so the root never re-serves/re-screenshots to show them. See the delegate→media bridge
    #: in :func:`gaia.core.screenshots.media_for_outputs`.
    media: list[str] = field(default_factory=list)
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


def _copy_into(primary: Path, src: Path) -> str | None:
    """Copy ``src`` into the workspace ``primary``; return its name, or ``None`` on failure.

    Skips if a file of that name is already there (don't clobber the soul's own work).
    Best-effort: a missing/unreadable source is logged and skipped, never fatal.
    """
    dest = primary / src.name
    try:
        primary.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copy2(src, dest)
        return src.name
    except OSError as exc:
        logger.warning("could not copy %s into %s: %s", src, primary, exc)
        return None


def _attach_uploads(primary: Path) -> list[str]:
    """Copy the turn's user attachments into the soul's workspace; return the relative names.

    A binary upload (e.g. an inbound image) lives in the shared uploads dir, outside the
    soul's workspace and unreachable over the http server that serves a built site. Copying
    it in gives the soul a real, relative file it can embed (``<img src="logo.jpg">``) and
    that the server actually serves. No attachments (the dispatcher path) → no-op.
    """
    return [name for src in inbound_attachments.get() if (name := _copy_into(primary, src))]


def _safe_attachment(raw: str) -> Path | None:
    """An input file path safe to copy into a soul's workspace, or ``None``.

    The trust boundary for agent-to-agent attachments: a caller (the root, or the dispatcher
    resolving an upstream task's artifacts) may name any path, so we accept it only if it is a
    real file under the agents tree or the uploads dir, and not a denied/secret file — a path
    arg can't pull arbitrary host files or secrets (``.env``/``.git``/keys) into a workspace.
    """
    resolved = Path(os.path.realpath(raw))
    roots = (
        Path(os.path.realpath(constants.AGENTS_DIR)),
        Path(os.path.realpath(constants.UPLOADS_DIR)),
    )
    if not resolved.is_file() or is_denied(resolved):
        return None
    if not any(resolved == r or resolved.is_relative_to(r) for r in roots):
        return None
    return resolved


def _attach_files(primary: Path, paths: list[str]) -> list[str]:
    """Copy validated input files into the soul's workspace; return the relative names.

    The agent-to-agent attachment path: files handed with a delegation (the root) or carried
    across a board dependency edge (the dispatcher) land in the soul's workspace exactly like a
    user's upload, so the soul reads/embeds them as relative files.
    """
    copied: list[str] = []
    for raw in paths:
        src = _safe_attachment(raw)
        if src is None:
            logger.warning("skipped attachment (not a readable file in the agents tree): %s", raw)
            continue
        if name := _copy_into(primary, src):
            copied.append(name)
    return copied


def _changed(before: dict[str, float], after: dict[str, float]) -> list[str]:
    """Relative paths created or modified between two snapshots (sorted, capped).

    Keeps the workspace flat — a reused soul's old, unrelated deliverables stay put but are
    NOT reported again; only what this run touched comes back.
    """
    return sorted(rel for rel, mtime in after.items() if before.get(rel) != mtime)[:MAX_FILES]


def _deliverable_media(primary: Path, files: list[str], run_media: list[str]) -> list[str]:
    """Absolute paths of media the soul produced for the user (de-duped, order-stable).

    Two sources: ``run_media`` (screenshots it took / files it sent, pulled from its event
    stream) and the workspace files it wrote whose type reads as a deliverable — real
    image/video/audio, or one of :data:`DELIVERABLE_DOC_SUFFIXES` (pdf/docx/xlsx/zip/…).
    Web-source files a site is made of (``.html``/``.css``/``.js``) are *not* media, so they're
    left out; the user gets the screenshot preview, not the source.
    """
    # ponytail: extension heuristic — generic over deliverable kinds (add to the suffix set as
    # they come up), but a site asset (a logo.png the soul generated) could still slip in. If
    # that gets noisy, have souls mark true deliverables explicitly (a soul-set output list).
    workspace = [
        str(primary / rel)
        for rel in files
        if media_kind(primary / rel) in ("image", "video", "audio")
        or (primary / rel).suffix.lower() in DELIVERABLE_DOC_SUFFIXES
    ]
    seen: dict[str, str] = {}
    for path in (*run_media, *workspace):
        seen.setdefault(str(Path(path).resolve()), path)
    return list(seen.values())


async def run_soul_agent(
    gaia: Gaia,
    soul: Any,
    key: str,
    task: str,
    user_id: str,
    *,
    state: dict[str, Any] | None = None,
    warm_key: str | None = None,
) -> tuple[str, list[str]]:
    """Run ``soul`` on ``task`` in a nested Runner; return its final text and any media.

    The runner is given Gaia's ``memory_service`` and the caller's ``user_id`` so the soul's
    ``load_memory``/``remember`` tools read and write the *same* user's long-term memory.

    ``state`` seeds the soul's ADK session state — the seam the soul's tools read to learn which
    task they're running (so ``task_create`` files a subtask of it) and the consult depth/chain
    that bounds in-turn recursion.

    ``warm_key`` (``soul/project``) keeps the session alive across delegations via
    :class:`gaia.souls.sessions.SoulSessionManager`, so the soul resumes instead of re-reading its
    workspace each time; ``None`` (the smith) uses a fresh throwaway session that must not persist.

    The media list is the paths of any files the soul produced for the user this run — extracted
    from its event stream with the same scanner the handler uses, so the root can deliver them.
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from gaia.core.plugins import ToolLoggingPlugin, ToolPermissionPlugin
    from gaia.core.screenshots import media_for_outputs

    if warm_key is not None:
        warm = await gaia.soul_sessions.acquire(
            warm_key, app_name=constants.APP_NAME, user_id=user_id, state=state
        )
        session_service, session_id, lock = warm.service, warm.session_id, warm.lock
    else:
        session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
        session_id = f"soul-{key}"
        await session_service.create_session(
            app_name=constants.APP_NAME, user_id=user_id, session_id=session_id, state=state or {}
        )
        lock = asyncio.Lock()  # uncontended; keeps the run path uniform

    runner = Runner(
        app_name=constants.APP_NAME,
        agent=soul,
        session_service=session_service,
        memory_service=gaia.memory_service,
        plugins=[ToolPermissionPlugin(gaia), ToolLoggingPlugin()],
    )
    content = types.Content(role="user", parts=[types.Part(text=task)])
    parts: list[str] = []
    events: list[Any] = []
    # NB: we deliberately never call ``runner.close()``. Runner.close() closes the agent's
    # toolsets — and the soul's tools include the *shared* MCP/Skills singletons (same objects on
    # the root agent, see AgentFactory); closing them mid-conversation makes the root's later turns
    # emit no model call (dead chat). They're closed once, by Gaia.close() at shutdown. A warm
    # session service is owned by SoulSessionManager; a throwaway one is GC'd here. The lock
    # serialises turns on a warm session (one ADK turn at a time; the dispatcher runs concurrently).
    async with lock:
        async for event in runner.run_async(
            user_id=user_id, session_id=session_id, new_message=content
        ):
            events.append(event)
            if event.is_final_response() and event.content and event.content.parts:
                parts.extend(p.text for p in event.content.parts if p.text)
    media = [str(m.path) for m in media_for_outputs(events)]
    return "\n".join(parts), media


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
    # The smith only emits a JSON decision (no screenshots/files), so its media is always empty.
    raw, _media = await run_soul_agent(gaia, smith, "smith", request, user_id="gaia")
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


def _project_slug(project: str, task: str) -> str:
    """The project dir slug for a run: the caller's name, else a fresh unique one.

    A named project lets the model continue an existing one (reuse the same slug) or start a
    new one. Omitted -> a unique slug derived from the task, so two unnamed runs of the same
    soul still get separate dirs instead of overwriting each other.
    """
    return _safe_dir(project) if project else f"{_safe_dir(task)[:24]}-{uuid4().hex[:6]}"


async def execute_decision(
    gaia: Gaia,
    decision: SoulDecision,
    task: str,
    user_id: str,
    *,
    project: str = "",
    attachments: list[str] | None = None,
    state: dict[str, Any] | None = None,
) -> SoulRun:
    """Build the chosen soul and run it on ``task``; capture the workspace diff as artifacts.

    The post-decision core shared by ``delegate_to_soul`` and the dispatcher: persist/build
    the soul (``create_or_reuse``), snapshot its workspace, run it (bounded by the configured
    ``souls.timeout_seconds``), and report the files it created/modified.

    The run is scoped to a **project** dir (``workspace/<project>``) so separate projects the
    same soul builds don't overwrite each other — ``project`` names it (reuse to continue one),
    else a fresh unique slug is used. ``state`` (e.g. the dispatched ``task_id``/``owner``) is
    seeded into the soul's session so its tools know which task they run; we also stamp
    ``created_by`` with the soul's own key so any subtask it files is attributed to it.
    """
    resolved = resolve_spec(gaia, decision)
    if resolved is None:
        return SoulRun(False, "", "", False, error="soul-smith returned no usable decision")
    spec, created = resolved

    from gaia.souls.consult import make_consult_soul

    # Every soul run can ask an expert (consult_soul); it needs the live gaia, so it's
    # threaded in per build rather than living in the static tool registry.
    soul = gaia.factory.create_or_reuse(
        spec, effort=gaia.config.llm.effort, extra_tools=[make_consult_soul(gaia)]
    )
    # Scope the whole run to the project dir: set the contextvar *before* resolving the
    # workspace, so the upload-copy, snapshot, the soul's own fs/exec tools, and the diff all
    # target workspace/<project>. Reset on exit so the caller (root Gaia) isn't left scoped.
    slug = _project_slug(project, task)
    token = current_project.set(slug)
    agent_token = current_agent.set(spec.key)  # so logs during this run are tagged with the soul
    try:
        primary = sandbox_for(constants.AGENTS_DIR, spec.key).primary
        # Bring the user's attachments into the workspace *before* the baseline snapshot, so
        # the copies aren't reported back as the soul's own deliverables.
        attached = _attach_uploads(primary)
        if attached:
            task = (
                f"{task}\n\n[The user's attached file(s) are in your workspace: "
                f"{', '.join(attached)} — use these relative names directly (e.g. "
                f'<img src="{attached[0]}">). Do not search the web for them or recreate them.]'
            )
        # Files sent with the delegation (the root) or carried across a board dependency edge
        # (the dispatcher) — copied in like an attachment so the soul builds on them directly.
        sent = _attach_files(primary, attachments or [])
        if sent:
            task = (
                f"{task}\n\n[File(s) sent with this task are in your workspace: "
                f"{', '.join(sent)} — use these relative names directly (read/embed them). "
                "Do not search the web for them or recreate them.]"
            )
        before = _snapshot(primary)
        timeout = gaia.config.souls.timeout_seconds  # read per call so yaml edits hot-reload
        run_state = {**(state or {}), "created_by": spec.key}
        try:
            summary, run_media = await asyncio.wait_for(
                run_soul_agent(
                    gaia,
                    soul,
                    spec.key,
                    task,
                    user_id,
                    state=run_state,
                    warm_key=f"{spec.key}/{slug}",  # keep this (soul, project) session warm
                ),
                timeout=timeout,
            )
        except TimeoutError:
            return SoulRun(
                False, spec.key, spec.name, created, error=f"soul timed out after {timeout:.0f}s"
            )
        except Exception as exc:
            return SoulRun(False, spec.key, spec.name, created, error=f"soul run failed: {exc}")

        files = _changed(before, _snapshot(primary))
        media = _deliverable_media(primary, files, run_media)
    finally:
        current_project.reset(token)
        current_agent.reset(agent_token)
    return SoulRun(
        ok=True,
        soul_key=spec.key,
        soul_name=spec.name,
        created=created,
        reason=decision.reason,
        summary=summary,
        workspace=str(primary),
        project=slug,
        files=files,
        media=media,
    )
