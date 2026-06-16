"""Present a finished mission deliverable to the user — Gaia opens and shows it.

Instead of the daemon blindly rendering, the privileged root agent (it can read every soul
workspace, #121) runs one turn to *present* a completed task: summarize what was produced
and, for a website, open the local deliverable in its browser and screenshot it. Gaia's
screenshots already become :class:`~gaia.connectors.base.Media` on the way out
(``core/handler._emit_reply`` → ``core/screenshots`` / #143), so the preview lands as a real
WhatsApp image — no bespoke render in the hot path.

If a deliverable needs fixing after presenting, Gaia (the master soul) decides to delegate
it back to the right agent — there's no special render/preview machinery here. Best-effort
throughout: the result is already safe on the board.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from gaia.connectors.base import Reply
from gaia.missions.notify import _target

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia
    from gaia.missions.store import Task
    from gaia.souls.run import SoulRun

logger = logging.getLogger(__name__)


def _prompt(task: Task, run: SoulRun) -> str:
    files = ", ".join(run.files) or "(none listed)"
    parts = [
        "A task you orchestrated is finished and ready to show the user — present it now.",
        f"Title: {task.title or task.id}",
        f"Result: {run.summary or '(no summary)'}",
        f"Deliverable files are in this workspace: {run.workspace}",
        f"Files produced: {files}",
        "",
        "Do ALL of this YOURSELF in THIS turn — you have file access to every soul's "
        "workspace (use the absolute paths above). Do NOT transfer_to_agent, do NOT "
        "delegate_to_soul, do NOT create tasks, and do NOT hand off to any specialist: if "
        "you transfer, the screenshot never reaches the user. Read the files if helpful, then "
        "give a short, useful summary of what was produced. If the deliverable is a website, "
        "call serve(<workspace>) to host it locally, then browser_navigate to the returned "
        "url (append the entry .html file if needed) and browser_screenshot RIGHT AWAY so the "
        "user sees a real render — do NOT use file://, it renders blank for real sites. "
        "Include the live url in your reply so the user can open it. Serve + open + "
        "screenshot + summarize, all here, then stop.",
    ]
    return "\n".join(parts)


async def present_result(gaia: Gaia, task: Task, run: SoulRun) -> None:
    """Run a Gaia turn that presents ``task``'s deliverable to its notify target (best-effort)."""
    target = _target(gaia, task)
    if target is None:
        logger.info("mission %s: no delivery target — deliverable stays on the board", task.id)
        return
    channel, chat = target
    sender = gaia.connectors.get(channel)
    if sender is None:
        logger.info("mission %s: connector %r not running — not presented", task.id, channel)
        return

    from gaia.core.handler import build_handler

    async def send(reply: Reply) -> None:
        await sender.send_to(chat, reply)

    handler = build_handler(gaia, user_id=task.owner or "gaia", session_id=f"mission-{task.id}")
    try:
        await handler(_prompt(task, run), send)
    except Exception:  # pragma: no cover - presentation is best-effort
        logger.warning("mission %s: present turn failed", task.id, exc_info=True)
