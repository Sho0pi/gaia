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
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from gaia import constants
from gaia.connectors.base import inbound_attachments, media_kind
from gaia.core.elicit import ASK_USER_TOOL, SoulPending
from gaia.souls.smith import SoulDecision, build_soul_smith
from gaia.tools.fs.base import _safe_dir, current_agent, current_project, is_denied, sandbox_for

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia
    from gaia.souls.projects import ProjectStore

logger = logging.getLogger(__name__)

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
    #: Set when the soul paused on ``ask_user`` mid-run (P2): the run is neither ok nor failed —
    #: it's waiting on the user. The caller surfaces the question and later calls ``resume_soul``.
    pending: SoulPending | None = None


@dataclass
class _AgentTurn:
    """One soul turn's outcome: its final text, media, and the ask_user call if it paused."""

    text: str
    media: list[str]
    paused: Any | None = None  # the soul's ask_user function_call (ADK), if the run paused


def _soul_ask_call(event: Any) -> Any | None:
    """The soul's ``ask_user`` function call in ``event`` if it paused the run, else None.

    Same shape as ``GaiaHandler._ask_call``: a long-running call flagged on
    ``event.long_running_tool_ids`` whose name is ``ask_user``.
    """
    ids = getattr(event, "long_running_tool_ids", None)
    if not ids or not (event.content and event.content.parts):
        return None
    for part in event.content.parts:
        call = getattr(part, "function_call", None)
        if call is not None and call.id in ids and call.name == ASK_USER_TOOL:
            return call
    return None


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


async def _run_soul_content(
    gaia: Gaia,
    soul: Any,
    key: str,
    user_id: str,
    content: Any,
    *,
    state: dict[str, Any] | None = None,
    warm_key: str | None = None,
) -> _AgentTurn:
    """Drive ``soul`` over one ADK turn for ``content`` (a task message or a resume
    function-response) and return its text/media — or the ask_user call if it paused.

    The runner is given Gaia's ``memory_service`` and the caller's ``user_id`` so the soul's
    ``load_memory``/``remember`` tools read and write the *same* user's long-term memory.
    ``warm_key`` (``soul/project``) keeps the session alive across delegations and across a
    pause, so a resume re-enters the same session (its events carry the paused ask_user call).
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService

    from gaia.core.plugins import ToolLoggingPlugin, ToolPermissionPlugin
    from gaia.core.screenshots import media_for_outputs

    # ponytail: souls stay on in-process warm InMemory sessions (not the durable store) — the
    # concurrent missions dispatcher sharing one SQLite session store breaks the subtask-yield run
    # loop. Durable souls is a follow-up (#310); the main handler's sessions are durable (#76).
    if warm_key is not None:
        warm = await gaia.soul_sessions.acquire(
            warm_key, app_name=constants.APP_NAME, user_id=user_id, state=state
        )
        session_service, session_id, lock = warm.session_service, warm.session_id, warm.lock
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
    parts: list[str] = []
    events: list[Any] = []
    paused: Any | None = None
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
            # The soul paused on ask_user. Record it but keep draining: a long-running tool
            # emits no function-response, so run_async ends on its own right after — letting it
            # finish closes the run cleanly (see GaiaHandler._drive for the same reasoning).
            if (call := _soul_ask_call(event)) is not None:
                paused = call
                continue
            if event.is_final_response() and event.content and event.content.parts:
                parts.extend(p.text for p in event.content.parts if p.text)
    media = [str(m.path) for m in media_for_outputs(events)]
    return _AgentTurn("\n".join(parts), media, paused)


async def run_soul_agent(
    gaia: Gaia,
    soul: Any,
    key: str,
    task: str,
    user_id: str,
    *,
    state: dict[str, Any] | None = None,
    warm_key: str | None = None,
) -> _AgentTurn:
    """Run ``soul`` on ``task`` in a nested Runner; return its turn outcome (text/media/pause)."""
    from google.genai import types

    content = types.Content(role="user", parts=[types.Part(text=task)])
    return await _run_soul_content(
        gaia, soul, key, user_id, content, state=state, warm_key=warm_key
    )


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
    # The smith only emits a JSON decision (no screenshots/files, never ask_user), so its turn is
    # always a plain text result.
    turn = await run_soul_agent(gaia, smith, "smith", request, user_id="gaia")
    return SoulDecision.model_validate(json.loads(turn.text))


def resolve_spec(gaia: Gaia, decision: SoulDecision) -> tuple[Any, bool] | None:
    """Turn a smith decision into a concrete ``AgentSpec`` + ``created`` flag, or ``None``."""
    known = gaia.souls.list_keys()
    if decision.action == "reuse" and decision.soul_key in known:
        spec = gaia.souls.get(decision.soul_key)
        return (spec, False) if spec is not None else None
    if decision.spec is not None:
        return decision.spec, decision.spec.key not in known
    return None


def _existing_projects(agents_dir: Path, soul_key: str) -> list[tuple[str, str]]:
    """Each project under a soul's workspace as ``(slug, description)`` (``_``-dirs excluded).

    The description is the project's ``PROJECT.md`` frontmatter (``""`` if none) — frontmatter
    only, never the body. ``read_project_description`` does the cheap parse.
    """
    from gaia.souls.projects import read_project_description

    base = agents_dir / _safe_dir(soul_key) / "workspace"
    if not base.is_dir():
        return []
    return sorted(
        (p.name, read_project_description(p))
        for p in base.iterdir()
        if p.is_dir() and not p.name.startswith("_")
    )


#: Short, common words that carry no project identity — ignored when matching task↔project.
_PROJECT_STOPWORDS = frozenset(
    "the and for with this that you your our build make create fix update edit add new app site "
    "project change extend work redo please can now also into from get put".split()
)


def _keywords(text: str) -> set[str]:
    """Significant lowercase words (len ≥ 3, non-stopword) of ``text`` — the matching vocabulary."""
    return {
        w
        for w in re.findall(r"[a-z0-9]+", text.lower())
        if len(w) >= 3 and w not in _PROJECT_STOPWORDS
    }


def _best_match(text: str, existing: list[tuple[str, str]]) -> str | None:
    """The existing project whose slug+description best overlaps ``text``'s keywords (≥2 shared).

    This is the semantic-ish reuse: a paraphrased/invented name (``hsk1-flashcards``) or a task that
    names an app ("the hsk flashcards login") lands on the matching project instead of forking. The
    ≥2 threshold avoids reusing on a single incidental shared word.
    """
    want = _keywords(text)
    if not want:
        return None
    best, best_score = None, 0
    for slug, desc in existing:
        score = len(want & _keywords(f"{slug} {desc}"))
        if score > best_score:
            best, best_score = slug, score
    return best if best_score >= 2 else None


def _project_description(task: str) -> str:
    """A one-line seed description for a new project, from its task (the soul refines it)."""
    return " ".join(task.split())[:160]


def resolve_project(
    project: str,
    task: str,
    user_id: str,
    soul_key: str,
    existing: list[tuple[str, str]],
    store: ProjectStore,
) -> str:
    """Pick the project dir for a run, routing by *meaning* so one app doesn't fork — and a
    different task cleanly *switches* projects.

    * named project: reuse the slug if it exists; else match it+the task against existing
      descriptions (an invented name for an existing app reuses it); else start it new.
    * omitted: if the task clearly describes an existing project, use it (continue OR switch); else
      continue the ``(user, soul)`` last project; else a fresh slug.

    The chosen project is remembered as current for ``(user, soul)`` (survives ``/reset``/restart).
    """
    slugs = {s for s, _ in existing}
    if project:
        slug = _safe_dir(project)
        chosen = slug if slug in slugs else (_best_match(f"{project} {task}", existing) or slug)
    else:
        match = _best_match(task, existing)
        if match is not None:
            chosen = match
        else:
            last = store.get(user_id, soul_key)
            chosen = last if last in slugs else f"{_safe_dir(task)[:24]}-{uuid4().hex[:6]}"
    store.set(user_id, soul_key, chosen)
    return chosen


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
        spec, effort=gaia.config.llm.effort, extra_tools=[make_consult_soul(gaia)], user_id=user_id
    )
    # Resolve the project dir, converging on an existing one so one app doesn't fork into a new
    # workspace every turn (see resolve_project). Scope the whole run to it: set the contextvar
    # *before* resolving the workspace, so the upload-copy, snapshot, the soul's own fs/exec tools,
    # and the diff all target workspace/<project>. Reset on exit so the root Gaia isn't left scoped.
    existing = _existing_projects(constants.AGENTS_DIR, spec.key)
    slug = resolve_project(project, task, user_id, spec.key, existing, gaia.projects)
    is_new = slug not in {s for s, _ in existing}
    if not is_new:  # continuing — point the soul at PROJECT.md, edit don't rebuild
        task = (
            f"{task}\n\n[You are CONTINUING the existing project '{slug}'. It has a PROJECT.md "
            f"(its description + rules) — fs_read it before editing, keep it updated, and edit the "
            f"existing files; do NOT rebuild it from scratch.]"
        )
    else:  # new project — the soul OWNS its PROJECT.md (you author it, like a skill)
        task = (
            f"{task}\n\n[This is a NEW project '{slug}'. A starter PROJECT.md is in your "
            f"workspace — refine its frontmatter `description` and write the project's key "
            f"details, decisions, and rules in the body; keep PROJECT.md updated as it evolves.]"
        )
    token = current_project.set(slug)
    agent_token = current_agent.set(spec.key)  # so logs during this run are tagged with the soul
    try:
        primary = sandbox_for(constants.AGENTS_DIR, spec.key).primary
        if is_new:  # seed PROJECT.md before the baseline snapshot so it isn't a "deliverable"
            from gaia.souls.projects import write_project_md

            write_project_md(primary, slug, _project_description(task))
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
        warm_key = f"{spec.key}/{slug}"  # keep this (soul, project) session warm
        try:
            turn = await asyncio.wait_for(
                run_soul_agent(
                    gaia, soul, spec.key, task, user_id, state=run_state, warm_key=warm_key
                ),
                timeout=timeout,
            )
        except TimeoutError:
            return SoulRun(
                False, spec.key, spec.name, created, error=f"soul timed out after {timeout:.0f}s"
            )
        except Exception as exc:
            return SoulRun(False, spec.key, spec.name, created, error=f"soul run failed: {exc}")

        if turn.paused is not None:
            # The soul asked the user mid-run: hand the pause up so the root surfaces the
            # question; ``before`` rides along so a later resume reports the cumulative diff.
            return SoulRun(
                False,
                spec.key,
                spec.name,
                created,
                project=slug,
                workspace=str(primary),
                pending=_soul_pending(turn.paused, warm_key, spec, slug, user_id, before),
            )

        files = _changed(before, _snapshot(primary))
        media = _deliverable_media(primary, files, turn.media)
    finally:
        current_project.reset(token)
        current_agent.reset(agent_token)
    return SoulRun(
        ok=True,
        soul_key=spec.key,
        soul_name=spec.name,
        created=created,
        reason=decision.reason,
        summary=turn.text,
        workspace=str(primary),
        project=slug,
        files=files,
        media=media,
    )


def soul_result(run: SoulRun) -> dict[str, Any]:
    """The tool-result dict for a finished soul run — ``delegate_to_soul``'s success/error
    return, also used to resume the root once a paused soul completes."""
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


def _soul_pending(
    call: Any, warm_key: str, spec: Any, slug: str, user_id: str, before: dict[str, float]
) -> SoulPending:
    """Build a :class:`SoulPending` from the soul's paused ``ask_user`` call."""
    args = call.args or {}
    return SoulPending(
        warm_key=warm_key,
        soul_key=spec.key,
        project=slug,
        soul_fc_id=call.id,
        question=str(args.get("question", "")),
        options=tuple(args.get("options") or ()),
        secret=bool(args.get("secret", False)),
        soul_name=spec.name,
        user_id=user_id,
        before=before,
    )


async def resume_soul(gaia: Gaia, pending: SoulPending, answer: str) -> SoulRun:
    """Resume the soul paused on ``ask_user`` by feeding ``answer`` as the tool's result.

    Re-enters the *same* warm session (its events carry the paused call), so the soul continues
    where it stopped — no smith re-run, no attachment re-copy. Returns a finished ``SoulRun``, or
    one whose ``pending`` is set again if the soul asked a further question.
    """
    from google.genai import types

    from gaia.souls.consult import make_consult_soul

    spec = gaia.souls.get(pending.soul_key)
    if spec is None:  # the soul was deleted between pause and answer
        return SoulRun(
            False, pending.soul_key, pending.soul_name, False, error="soul no longer exists"
        )
    soul = gaia.factory.create_or_reuse(
        spec,
        effort=gaia.config.llm.effort,
        extra_tools=[make_consult_soul(gaia)],
        user_id=pending.user_id,
    )
    token = current_project.set(pending.project)
    agent_token = current_agent.set(spec.key)
    try:
        primary = sandbox_for(constants.AGENTS_DIR, spec.key).primary
        timeout = gaia.config.souls.timeout_seconds
        fr = types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        id=pending.soul_fc_id, name=ASK_USER_TOOL, response={"answer": answer}
                    )
                )
            ],
        )
        try:
            turn = await asyncio.wait_for(
                _run_soul_content(
                    gaia, soul, spec.key, pending.user_id, fr, warm_key=pending.warm_key
                ),
                timeout=timeout,
            )
        except TimeoutError:
            return SoulRun(
                False,
                spec.key,
                spec.name,
                False,
                project=pending.project,
                error=f"soul timed out after {timeout:.0f}s",
            )
        except Exception as exc:
            return SoulRun(
                False,
                spec.key,
                spec.name,
                False,
                project=pending.project,
                error=f"soul run failed: {exc}",
            )

        if turn.paused is not None:  # the soul asked something else — pause again
            return SoulRun(
                False,
                spec.key,
                spec.name,
                False,
                project=pending.project,
                workspace=str(primary),
                pending=_soul_pending(
                    turn.paused,
                    pending.warm_key,
                    spec,
                    pending.project,
                    pending.user_id,
                    pending.before,
                ),
            )

        # Cumulative diff against the baseline taken before the soul first ran (carried across
        # the pause in ``pending.before``), so files written before the question are reported too.
        files = _changed(pending.before, _snapshot(primary))
        media = _deliverable_media(primary, files, turn.media)
    finally:
        current_project.reset(token)
        current_agent.reset(agent_token)
    return SoulRun(
        ok=True,
        soul_key=spec.key,
        soul_name=spec.name,
        created=False,
        summary=turn.text,
        workspace=str(primary),
        project=pending.project,
        files=files,
        media=media,
    )
