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
from gaia.logs import log_error

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia
    from gaia.missions.store import Task
    from gaia.souls.run import SoulRun

logger = logging.getLogger(__name__)

#: Suffixes we deliver inline as an image rather than just naming the path.
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")

#: Text deliverables whose *content* we send back (the soul writes the real answer to a
#: file and only summarizes "done" — the user wants the content, not the filename). ``.html``
#: is deliberately absent: a website is *presented* by Gaia (it opens + screenshots it),
#: not dumped as source here.
_TEXT_SUFFIXES = (".md", ".txt", ".csv", ".json", ".py", ".js", ".ts", ".yaml")

#: Cap per text artifact and overall, so a huge deliverable doesn't flood the chat.
_PER_FILE_CHARS = 3000
_TOTAL_CHARS = 8000


def _artifact_text(workspace: str, files: list[str]) -> str:
    """Read text deliverables and render them as labelled blocks (capped)."""
    blocks: list[str] = []
    budget = _TOTAL_CHARS
    for rel in files:
        if budget <= 0 or not rel.lower().endswith(_TEXT_SUFFIXES):
            continue
        path = Path(workspace) / rel if workspace else Path(rel)
        try:
            body = path.read_text(errors="replace")
        except OSError:
            continue
        clipped = body[:_PER_FILE_CHARS]
        if len(body) > _PER_FILE_CHARS:
            clipped += "\n… (truncated)"
        block = f"📄 {rel}\n{clipped}"[:budget]
        budget -= len(block)
        blocks.append(block)
    return "\n\n".join(blocks)


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


async def _push(gaia: Gaia, task: Task, text: str) -> None:
    """Best-effort plain-text push to ``task``'s target chat (no artifacts)."""
    target = _target(gaia, task)
    if target is None:
        logger.info("mission %s: no delivery target — message not pushed", task.id)
        return
    channel, chat = target
    sender = gaia.connectors.get(channel)
    if sender is None:
        logger.info("mission %s: connector %r not running — message not pushed", task.id, channel)
        return
    try:
        await sender.send_to(chat, text)
    except Exception as exc:  # pragma: no cover - delivery is best-effort
        log_error("mission_notify", exc, task=task.id)


async def notify_approval(gaia: Gaia, task: Task) -> None:
    """Ask the human to approve a gated task parked in ``awaiting_approval``."""
    cls = task.approval_class or "action"
    await _push(
        gaia,
        task,
        f"⏸ {task.title or task.id} needs approval ({cls}).\n"
        f"Reply: /task approve {task.id}  (or /task reject {task.id})",
    )


async def notify_paused(gaia: Gaia, task: Task, reason: str) -> None:
    """Tell the human a mission was paused (a cap was hit) and how to resume it."""
    await _push(
        gaia,
        task,
        f"⏸ Mission {task.title or task.id} paused — {reason}.\n"
        f"Reply: /task approve {task.id} to continue, or /task reject {task.id} to stop.",
    )


async def notify_rejected(gaia: Gaia, task: Task) -> None:
    """Tell the human a gated task was rejected (and so the mission step won't run)."""
    await _push(gaia, task, f"✗ {task.title or task.id} was rejected — it won't run.")


async def notify_ask_user(
    gaia: Gaia, task: Task, question: str, options: tuple[str, ...] = ()
) -> None:
    """Ask the human a question a background-mission soul raised mid-run (P3).

    Pushed out-of-band to the task's target chat; the user answers with ``/task answer``.
    Options (if any) render as a numbered list the user picks from in their reply.
    """
    lines = [f"❓ {task.title or task.id}: {question}"]
    lines += [f"  {i}. {opt}" for i, opt in enumerate(options, 1)]
    lines.append(f"Reply: /task answer {task.id} <your answer>")
    await _push(gaia, task, "\n".join(lines))


async def notify_result(gaia: Gaia, task: Task, run: SoulRun) -> None:
    """Best-effort push of a completed task's actual deliverable to its target chat.

    Sends the soul's summary **plus the content of any text deliverables** (the soul writes
    the real answer to a file and only summarizes "done"), then any image artifacts inline.
    """
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
    head = f"✓ {task.title or task.id}\n\n{summary}"
    try:
        # Summary + inline text-deliverable content, then any image artifacts. A website is
        # not rendered here — Gaia presents those (see present_result); this path covers
        # failures and plain text/image results.
        body = _artifact_text(run.workspace, run.files)
        text = f"{head}\n\n{body}" if body else head
        await sender.send_to(chat, text)
        for path in run.files:
            if path.lower().endswith(_IMAGE_SUFFIXES):
                full = Path(run.workspace) / path if run.workspace else Path(path)
                await sender.send_to(chat, Media(path=full))
    except Exception as exc:  # pragma: no cover - delivery is best-effort
        log_error("mission_deliver", exc, task=task.id)
