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
        "Give the user a short, useful summary of what was produced (read the files if "
        "helpful — you can access the workspace). If the deliverable is a website, OPEN it in "
        "your browser via its local file path (file://<workspace>/<the .html file>) and take a "
        "screenshot so they can see it. Do NOT create new tasks or delegate — just present.",
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
