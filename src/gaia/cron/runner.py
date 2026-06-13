"""Turn a fired cron job into a system-initiated agent turn + proactive delivery.

The god-PR ``RunInstruction`` shape: the job's message runs through the normal handler
machinery (so the turn gets Gaia's full tools/skills/memory), and every reply streams
out through the connector's ``send_to`` — to the chat that created the job, falling
back to the configured ``cron.deliver`` default, else the log. A failed delivery or
turn is logged and dropped; the scheduler loop must never die for one bad job.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol

from gaia.connectors.base import Reply, as_text
from gaia.cron.store import CronJob

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia

logger = logging.getLogger(__name__)


class ProactiveSender(Protocol):
    """What the runner needs from a connector: push a reply to a chat id."""

    async def send_to(self, chat: str, reply: Reply) -> None: ...


def make_runner(gaia: Gaia) -> Any:  # Runner (Callable[[CronJob], Awaitable[None]])
    """Build the scheduler's runner bound to ``gaia``.

    The live connector registry is read from ``gaia.connectors`` (the container's shared
    dict the launcher populates). We deliberately do NOT use dependency-injector
    ``@inject`` here — its ``wire()`` only patches *module-level* functions, so this
    per-call closure is invisible to it, and wiring binds ``Provide`` markers at module
    (global) scope, which clashes with the per-``Gaia`` container. See #146 for the spike.
    """
    from gaia.core.handler import build_handler

    async def run(job: CronJob) -> None:
        channel, chat = _delivery_target(gaia, job)
        sender = gaia.connectors.get(channel)

        async def send(reply: Reply) -> None:
            if sender is None:
                logger.warning(
                    "cron job %s reply dropped (no connector %r running): %s",
                    job.id,
                    channel,
                    as_text(reply)[:200],
                )
                return
            await sender.send_to(chat, reply)

        # A fresh handler per fire: its own session (`cron-<id>`), so scheduled turns
        # don't leak into (or inherit) the live conversation.
        handler = build_handler(gaia, user_id="gaia-cron", session_id=f"cron-{job.id}")
        prompt = (
            f"[scheduled task — fired by your cron schedule, not the user] {job.message}\n"
            "Carry the task out now and reply with the result for the user."
        )
        await handler(prompt, send)

    return run


def _delivery_target(gaia: Gaia, job: CronJob) -> tuple[str, str]:
    """The (channel, chat) to deliver to: the job's own, else the configured default."""
    if job.channel and job.chat:
        return job.channel, job.chat
    deliver = gaia.config.cron.deliver
    return deliver.channel, deliver.chat
