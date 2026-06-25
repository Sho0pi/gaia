"""One monitor cycle: digest recent errors -> health analyst -> DM the admin about new findings.

Mirrors :mod:`gaia.analysis.loop`. PR2 action is **notify the admin** (GitHub issues land in PR3).
Dedup (:mod:`gaia.monitor.state`) keeps the same error signature from being re-reported every cycle.
Best-effort throughout — a quiet window just does nothing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import ValidationError

from gaia.logs import log_error, log_event

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia
    from gaia.monitor.analyst import Finding, HealthReport

logger = logging.getLogger(__name__)


async def analyze(gaia: Gaia) -> HealthReport | None:
    """Digest recent errors and run the health analyst — no side effects. None if nothing found."""
    from gaia.analysis.events import read_events
    from gaia.monitor.digest import error_digest, render_error_digest

    window_hours = gaia.config.monitor.window_hours
    events = read_events(gaia.settings.log_dir, datetime.now() - timedelta(hours=window_hours))
    digest = error_digest(events)
    if not digest.groups:
        return None
    try:
        return await _run_analyst(gaia, render_error_digest(digest))
    except ValidationError:
        # The model returned unparseable/off-schema output — expected flakiness on weaker models,
        # not a code bug. Skip quietly (no error event, or the monitor would report itself).
        logger.warning("monitor: analyst returned invalid output — skipping this cycle")
        return None
    except Exception as exc:
        log_error("monitor_loop", exc)  # traceback -> system.log + event -> events.jsonl
        return None


async def run_cycle(gaia: Gaia) -> list[Finding]:
    """Run one monitor cycle; return the findings reported. Empty when healthy or all deduped."""
    from gaia.monitor.state import filter_new

    report = await analyze(gaia)
    if report is None:
        return []
    actionable = [f for f in report.findings if f.action != "ignore"]
    if not actionable:
        return []
    # ponytail: cooldown == cycle interval, so a recurring error is reported at most once per cycle
    cooldown = gaia.config.monitor.interval_hours
    fresh = set(filter_new([f.signature or f.title for f in actionable], cooldown))
    new = [f for f in actionable if (f.signature or f.title) in fresh]
    if not new:
        return []
    if gaia.config.monitor.notify:
        await _notify_admin(gaia, report.summary, new)
    await _file_issues(gaia, new)
    log_event("monitor_reported", count=len(new), summary=report.summary[:200])
    return new


async def _run_analyst(gaia: Gaia, digest_text: str) -> HealthReport:
    """Drive the health analyst over the error digest and parse its structured report."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from gaia import constants
    from gaia.monitor.analyst import HealthReport, build_health_analyst

    cfg = gaia.config
    analyst = build_health_analyst(
        cfg.llm.model, provider=cfg.llm.provider, use_oauth=cfg.llm.openai.use_oauth
    )
    session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
    await session_service.create_session(
        app_name=constants.APP_NAME, user_id="monitor", session_id="health"
    )
    runner = Runner(app_name=constants.APP_NAME, agent=analyst, session_service=session_service)
    content = types.Content(role="user", parts=[types.Part(text=f"ERROR DIGEST:\n{digest_text}")])
    parts: list[str] = []
    try:
        async for event in runner.run_async(
            user_id="monitor", session_id="health", new_message=content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                parts.extend(p.text for p in event.content.parts if p.text)
    finally:
        await runner.close()  # type: ignore[no-untyped-call]
    return HealthReport.model_validate_json("".join(parts))


async def _file_issues(gaia: Gaia, findings: list[Finding]) -> None:
    """File a GitHub issue per file_issue finding (deduped against GitHub). Opt-in + best-effort."""
    gh = gaia.config.monitor.github
    to_file = [f for f in findings if f.action == "file_issue"]
    if not (gh.create_issues and to_file):
        return
    token = gaia.settings.github_token
    if not token or not gh.repo:
        logger.warning("monitor: create_issues on but GITHUB_TOKEN/repo missing — skipping issues")
        return
    from gaia.monitor.github import file_issue

    for f in to_file:
        try:
            url = await file_issue(
                gh.repo,
                token,
                signature=f.signature or f.title,
                title=f.title or f.signature,
                body=f.issue_body or f.summary,
                label=gh.label,
            )
            log_event("monitor_issue", signature=f.signature, url=url)
        except Exception as exc:
            log_error("monitor_github", exc)


async def _notify_admin(gaia: Gaia, summary: str, findings: list[Finding]) -> None:
    """Best-effort: DM the first reachable admin the health findings."""
    from gaia.tools.message import user_address

    admins = [u for u in gaia.users.list() if u.role == "admin" and u.identities]
    if not admins:
        return
    lines = [f"⚠ gaia health check: {summary}", ""]
    for f in findings:
        lines.append(f"[{f.severity}] {f.title}")
        if f.summary:
            lines.append(f"  {f.summary}")
    text = "\n".join(lines)
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
