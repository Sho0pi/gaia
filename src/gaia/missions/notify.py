"""Push a finished mission's result to the chat that should hear about it.

The board is the source of truth (result + artifacts live on the task); this is the thin
"tell the human it's ready" layer. The delivery target is resolved in priority order:

1. the task's ``notify_channel``/``notify_chat`` (the chat it was filed from),
2. else the ``owner``'s user identity (reuses :func:`gaia.tools.message.user_address`),
3. else the configured ``cron.deliver`` default.

Delivery goes through the daemon's live connector registry (``gaia.connectors``); outside
the daemon (or with no matching connector running) it's logged and skipped — never fatal,
the result is already safe on the board.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from gaia.connectors.base import Media

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia
    from gaia.missions.store import Task
    from gaia.souls.run import SoulRun

logger = logging.getLogger(__name__)

#: Suffixes we deliver inline as an image rather than just naming the path.
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _target(gaia: Gaia, task: Task) -> tuple[str, str] | None:
    """The ``(channel, chat)`` to deliver ``task``'s result to, or ``None`` if undeliverable."""
    if task.notify_channel and task.notify_chat:
        return task.notify_channel, task.notify_chat
    if task.owner:
        from gaia.tools.message import user_address

        addr = user_address(gaia.users, task.owner)
        if addr is not None:
            return addr
    deliver = gaia.config.cron.deliver
    return (deliver.channel, deliver.chat) if deliver.channel and deliver.chat else None


async def notify_result(gaia: Gaia, task: Task, run: SoulRun) -> None:
    """Best-effort push of a completed task's result + image artifacts to its target chat."""
    target = _target(gaia, task)
    if target is None:
        logger.info("mission %s: no delivery target — result stays on the board only", task.id)
        return
    channel, chat = target
    sender = gaia.connectors.get(channel)
    if sender is None:
        logger.info(
            "mission %s: connector %r not running — result not pushed (on the board)",
            task.id,
            channel,
        )
        return

    summary = run.summary.strip() or "(no summary)"
    text = f"✓ {task.title or task.id} done: {summary}"
    try:
        await sender.send_to(chat, text)
        for path in run.files:
            if path.lower().endswith(_IMAGE_SUFFIXES):
                full = Path(run.workspace) / path if run.workspace else Path(path)
                await sender.send_to(chat, Media(path=full))
    except Exception:  # pragma: no cover - delivery is best-effort
        logger.warning("mission %s: result delivery failed", task.id, exc_info=True)
