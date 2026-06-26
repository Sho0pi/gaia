"""Pending human-in-the-loop elicitation state for a paused agent run.

When the model calls ``ask_user`` (a long-running tool) the run pauses: ADK emits the
function call but no response, so ``run_async`` completes. ``GaiaHandler`` records the
open question here, surfaces it, and resolves the user's next message back to an answer
fed in as the tool's ``FunctionResponse`` to resume the same run. Kept in this small,
ADK-free module so the resolve logic is unit-testable without a runner.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field

#: The ``ask_user`` tool id (mirrors ``gaia.tools.ask_user.NAME``). Duplicated as a plain
#: literal so the handler need not import the tool module (which pulls in ADK at import).
ASK_USER_TOOL = "ask_user"

#: The ``delegate_to_soul`` tool id (mirrors ``gaia.souls.delegate.NAME``). The handler treats
#: a long-running pause on this tool as a *soul* asking the user (P2), via :class:`SoulPending`.
DELEGATE_TOOL = "delegate_to_soul"


@dataclass
class SoulPending:
    """A delegated soul's ``ask_user`` pause — enough to resume that exact soul run.

    A soul runs in a nested Runner inside ``delegate_to_soul``; when it calls ``ask_user`` its
    run ends at the pause. This captures which warm session/soul/workspace to re-enter and the
    soul's own ``ask_user`` call id, so the answer resumes the *same* run (see
    ``gaia.souls.run.resume_soul``). ``before`` is the workspace snapshot taken before the soul
    first ran, carried across the pause so the final file diff is cumulative.
    """

    warm_key: str  # (soul, project) key to re-acquire the warm session
    soul_key: str  # which soul spec to rebuild
    project: str  # workspace/<project> slug
    soul_fc_id: str  # the soul's ask_user function_call id to resume
    question: str
    options: tuple[str, ...] = ()
    secret: bool = False
    soul_name: str = ""
    user_id: str = ""
    before: dict[str, float] = field(default_factory=dict)


#: Per-turn channel from a paused ``delegate_to_soul`` up to the handler. The handler installs a
#: fresh list before each model turn; if the delegated soul pauses on ``ask_user``, the tool
#: appends its :class:`SoulPending` here. A contextvar-carried *shared list* (mutated by the tool,
#: read by the handler) instead of a user-keyed dict makes it robust to ADK's
#: ``ToolContext.user_id`` not matching the session user — the bug that dropped soul answers.
soul_elicitation_sink: ContextVar[list[SoulPending] | None] = ContextVar(
    "soul_elicitation_sink", default=None
)


def soul_pending_to_json(pending: SoulPending) -> str:
    """Serialize a :class:`SoulPending` for durable storage on a Task row (P3)."""
    return json.dumps(asdict(pending))


def soul_pending_from_json(raw: str) -> SoulPending:
    """Rebuild a :class:`SoulPending` persisted by :func:`soul_pending_to_json`.

    ``options`` round-trips through JSON as a list — restore it to the dataclass's tuple.
    """
    data = json.loads(raw)
    data["options"] = tuple(data.get("options") or ())
    return SoulPending(**data)


@dataclass(frozen=True)
class Pending:
    """A question awaiting the user's reply, keyed to the paused call.

    ``soul`` is None for a root ``ask_user`` (P1): ``fc_id`` is the ask_user call to resume.
    When ``soul`` is set (P2), ``fc_id`` is the root ``delegate_to_soul`` call, and the answer
    first resumes the nested soul; only when the soul finishes is the delegate call resumed.
    """

    fc_id: str
    options: tuple[str, ...] = ()
    secret: bool = False
    soul: SoulPending | None = None


def resolve_answer(pending: Pending, text: str) -> str:
    """Map the user's raw reply to the answer string fed back to the model.

    For a multiple-choice question a numbered reply ("2") selects that option, and a
    WhatsApp interactive tap (arriving as ``"[Selected: X]"``) matches an option by
    label; anything else — and every free-text/secret answer — passes through verbatim.
    """
    raw = text.strip()
    if pending.options:
        if raw.startswith("[Selected:") and raw.endswith("]"):
            label = raw[len("[Selected:") : -1].strip().casefold()
            for opt in pending.options:
                if opt.strip().casefold() == label:
                    return opt
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(pending.options):
                return pending.options[idx]
    return raw
