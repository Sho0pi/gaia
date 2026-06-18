"""``gaia analyze`` — mine the usage events into skills / memories, human-approved.

The growth loop (issue #19): events.jsonl is reduced to a compact digest **in code**
(:mod:`gaia.analysis.events`), the analyst LLM proposes skills / memory writes from
that digest, and every proposal is confirmed in the terminal before anything is
written. ``--json`` stops after the digest (offline, no model call) for inspection.

Lazy-import rule (repo convention): only typer + stdlib (+ cli siblings) at module
level; analysis / ADK / mem0 load inside the command.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Annotated

import typer

from gaia.cli._console import console, emit_json
from gaia.cli._options import state

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.analysis import AnalysisReport, EventDigest
    from gaia.config import GaiaConfig

# Argument/option types named once so the command signature below stays readable.
DaysOpt = Annotated[int, typer.Option("--days", help="How many days of events to analyze.")]
UserOpt = Annotated[
    str | None, typer.Option("--user", help="Memory-write target; default: the single user seen.")
]
YesOpt = Annotated[
    bool, typer.Option("--yes", "-y", help="Approve every proposal without prompting.")
]


def analyze(
    ctx: typer.Context,
    days: DaysOpt = 7,
    user: UserOpt = None,
    yes: YesOpt = False,
) -> None:
    """Mine recent usage into proposed skills/memories (each needs your approval)."""
    from gaia.analysis import digest_events, read_events, render_digest
    from gaia.config import ConfigSupplier, configure_adk_env, get_settings

    settings = get_settings(state(ctx).env_file)
    out = console()

    events = read_events(settings.log_dir, datetime.now() - timedelta(days=days))
    if not events:
        out.print(f"no events in the last {days} day(s) — nothing to analyze")
        raise typer.Exit(1)
    digest = digest_events(events)

    if state(ctx).json:  # offline inspection mode: digest only, no model call
        emit_json(digest.model_dump())
        return

    cfg = ConfigSupplier(settings.config_path).current
    configure_adk_env(settings)
    out.print(render_digest(digest))
    out.print()
    try:
        report = _run_analyst_sync(cfg, render_digest(digest))
    except Exception as exc:
        out.print(f"analyst failed (is a model key configured?): {exc}")
        raise typer.Exit(1) from exc

    out.print(f"[bold]{report.summary}[/]\n")
    if not report.skills and not report.memories:
        out.print("no proposals this window — keep using gaia and re-run later")
        return

    written, saved, declined = _review(ctx, digest, report, user=user, yes=yes)
    out.print(f"\ndone: {written} skill(s) written, {saved} memory(ies) saved, {declined} declined")


def _review(
    ctx: typer.Context,
    digest: EventDigest,
    report: AnalysisReport,
    *,
    user: str | None,
    yes: bool,
) -> tuple[int, int, int]:
    """Walk every proposal through HITL approval; return (skills, memories, declined)."""
    from gaia.config import ConfigSupplier, get_settings
    from gaia.skills import resolve_skills_dir, write_skill

    settings = get_settings(state(ctx).env_file)
    cfg = ConfigSupplier(settings.config_path).current
    skills_dir = resolve_skills_dir(cfg)
    out = console()
    written = saved = declined = 0

    for skill in report.skills:
        out.print(f"\n[bold cyan]skill proposal:[/] {skill.name} — {skill.description}")
        out.print(f"rationale: {skill.rationale}")
        out.print(f"instructions:\n{skill.instructions}")
        if yes or typer.confirm("write this skill?"):
            try:
                folder = write_skill(skills_dir, skill.name, skill.description, skill.instructions)
                out.print(f"wrote {folder}")
                written += 1
            except (FileExistsError, ValueError, OSError) as exc:
                out.print(f"[yellow]skipped:[/] {exc}")
                declined += 1
        else:
            declined += 1

    for memory in report.memories:
        target = user or memory.user_id or _default_user(digest)
        if target is None:
            out.print("\n[yellow]memory proposal skipped:[/] several users seen — pass --user")
            declined += 1
            continue
        out.print(f"\n[bold cyan]memory proposal[/] (user {target}): {memory.fact}")
        out.print(f"rationale: {memory.rationale}")
        if yes or typer.confirm("save this memory?"):
            try:
                _save_memory(ctx, target, memory.fact)
                saved += 1
            except Exception as exc:
                out.print(f"[yellow]skipped:[/] {exc}")
                declined += 1
        else:
            declined += 1
    return written, saved, declined


def _default_user(digest: EventDigest) -> str | None:
    """The single user seen in the window, or ``None`` when it is ambiguous."""
    return next(iter(digest.users)) if len(digest.users) == 1 else None


def _run_analyst_sync(cfg: GaiaConfig, digest_text: str) -> AnalysisReport:
    """Run the analyst one-shot (nested Runner) and return its structured report."""
    import asyncio

    return asyncio.run(_run_analyst(cfg, digest_text))


async def _run_analyst(cfg: GaiaConfig, digest_text: str) -> AnalysisReport:
    """Drive the analyst via a fresh Runner; parse its final structured response."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from gaia import constants
    from gaia.analysis import AnalysisReport, build_analyst

    analyst = build_analyst(
        cfg.llm.model, provider=cfg.llm.provider, use_oauth=cfg.llm.openai.use_oauth
    )
    session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
    await session_service.create_session(
        app_name=constants.APP_NAME, user_id="cli", session_id="analyze"
    )
    runner = Runner(app_name=constants.APP_NAME, agent=analyst, session_service=session_service)
    content = types.Content(role="user", parts=[types.Part(text=f"DIGEST:\n{digest_text}")])
    parts: list[str] = []
    try:
        async for event in runner.run_async(
            user_id="cli", session_id="analyze", new_message=content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                parts.extend(p.text for p in event.content.parts if p.text)
    finally:
        await runner.close()  # type: ignore[no-untyped-call]
    return AnalysisReport.model_validate_json("".join(parts))


def _save_memory(ctx: typer.Context, user_id: str, fact: str) -> None:
    """Write one approved fact to long-term memory (mem0), synchronously."""
    import asyncio

    from google.adk.memory.memory_entry import MemoryEntry
    from google.genai import types

    from gaia import constants
    from gaia.config import ConfigSupplier, get_settings
    from gaia.memory import Mem0MemoryService, build_mem0

    settings = get_settings(state(ctx).env_file)
    cfg = ConfigSupplier(settings.config_path).current
    if not cfg.memory.enabled:
        raise RuntimeError("long-term memory is disabled in gaia.yaml (memory.enabled)")
    service = Mem0MemoryService(build_mem0(settings, cfg.memory))
    entry = MemoryEntry(content=types.Content(parts=[types.Part(text=fact)]), author="user")
    asyncio.run(service.add_memory(app_name=constants.APP_NAME, user_id=user_id, memories=[entry]))
