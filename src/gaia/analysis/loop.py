"""One self-improve cycle: digest recent usage -> analyst -> apply -> notify the owner.

Wired into the daemon by :class:`gaia.analysis.scheduler.AnalysisScheduler` (periodic) and
runnable on demand. Reads the events the daemon already logs, mines a digest, runs the
analyst (seeded with the existing skills/souls so it dedupes + chooses refine vs create),
applies the report autonomously (:func:`gaia.analysis.apply.apply_report`), and best-effort
tells the owner what changed. Best-effort throughout — a thin window just does nothing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from gaia.logs import log_event

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.analysis.analyst import AnalysisReport
    from gaia.config import GaiaConfig
    from gaia.core.agent import Gaia

logger = logging.getLogger(__name__)


async def analyze(gaia: Gaia) -> tuple[AnalysisReport | None, str | None]:
    """Digest recent usage and run the analyst — no writes. Returns (report, single_user)."""
    from gaia.analysis.events import digest_events, read_events, render_digest

    window_days = gaia.config.analysis.window_days
    events = read_events(gaia.settings.log_dir, datetime.now() - timedelta(days=window_days))
    if not events:
        return None, None
    digest = digest_events(events)
    try:
        report = await _run_analyst(gaia, render_digest(digest))
    except Exception as exc:
        logger.warning("improve cycle: analyst failed: %s", exc)
        return None, None
    return report, _single_user(digest)


async def run_cycle(gaia: Gaia) -> list[str]:
    """Run one improve cycle; return a line per applied change (empty when nothing changed)."""
    from gaia.analysis.apply import apply_report

    report, single_user = await analyze(gaia)
    if report is None:
        return []
    applied = await apply_report(gaia, report, user_id=single_user)
    if applied:
        log_event("improved", count=len(applied), summary=report.summary[:200])
        await _notify_owner(gaia, applied)
    return applied


def _single_user(digest: object) -> str | None:
    users = getattr(digest, "users", {})
    return next(iter(users)) if len(users) == 1 else None


async def _run_analyst(gaia: Gaia, digest_text: str) -> AnalysisReport:
    """Drive the analyst (seeded with existing skills/souls) and parse its structured report."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from gaia import constants
    from gaia.analysis import AnalysisReport, build_analyst

    cfg: GaiaConfig = gaia.config
    analyst = build_analyst(
        cfg.llm.model,
        provider=cfg.llm.provider,
        use_oauth=cfg.llm.openai.use_oauth,
        existing_skills=_existing_skills(gaia),
        existing_souls=_existing_souls(gaia),
    )
    session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
    await session_service.create_session(
        app_name=constants.APP_NAME, user_id="analyst", session_id="improve"
    )
    runner = Runner(app_name=constants.APP_NAME, agent=analyst, session_service=session_service)
    content = types.Content(role="user", parts=[types.Part(text=f"DIGEST:\n{digest_text}")])
    parts: list[str] = []
    try:
        async for event in runner.run_async(
            user_id="analyst", session_id="improve", new_message=content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                parts.extend(p.text for p in event.content.parts if p.text)
    finally:
        await runner.close()  # type: ignore[no-untyped-call]
    return AnalysisReport.model_validate_json("".join(parts))


def _existing_skills(gaia: Gaia) -> list[str]:
    from gaia.skills import list_skill_ids, load_skill, resolve_skills_dir

    skills_dir = resolve_skills_dir(gaia.config)
    out = []
    for skill_id in list_skill_ids(skills_dir):
        skill = load_skill(skills_dir, skill_id)
        out.append(f"{skill_id} — {skill.frontmatter.description if skill else ''}")
    return out


def _existing_souls(gaia: Gaia) -> list[str]:
    out = []
    for key in gaia.souls.list_keys():
        spec = gaia.souls.get(key)
        out.append(f"{key} — {spec.description if spec else ''}")
    return out


async def _notify_owner(gaia: Gaia, applied: list[str]) -> None:
    """Best-effort: tell the first reachable admin what gaia changed this cycle."""
    from gaia.tools.message import user_address

    admins = [u for u in gaia.users.list() if u.role == "admin" and u.identities]
    if not admins:
        return
    text = "I improved myself based on recent usage:\n" + "\n".join(f"- {line}" for line in applied)
    for admin in admins:
        addr = user_address(gaia.users, admin.id)
        if addr is None:
            continue
        channel, chat = addr
        sender = gaia.connectors.get(channel)
        if sender is not None:
            try:
                await sender.send_to(chat, text)
                return
            except Exception:
                continue
