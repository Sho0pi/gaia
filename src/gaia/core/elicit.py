"""Pending human-in-the-loop elicitation state for a paused agent run.

When the model calls ``ask_user`` (a long-running tool) the run pauses: ADK emits the
function call but no response, so ``run_async`` completes. ``GaiaHandler`` records the
open question here, surfaces it, and resolves the user's next message back to an answer
fed in as the tool's ``FunctionResponse`` to resume the same run. Kept in this small,
ADK-free module so the resolve logic is unit-testable without a runner.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

#: The ``ask_user`` tool id (mirrors ``gaia.tools.ask_user.NAME``). Duplicated as a plain
#: literal so the handler need not import the tool module (which pulls in ADK at import).
ASK_USER_TOOL = "ask_user"


@dataclass(frozen=True)
class Pending:
    """A question awaiting the user's reply, keyed to the paused ``ask_user`` call."""

    fc_id: str  # the ask_user function_call id to resume with a FunctionResponse
    options: tuple[str, ...] = ()
    secret: bool = False


def resolve_answer(pending: Pending, text: str) -> str:
    """Map the user's raw reply to the answer string fed back to the model.

    For a multiple-choice question: a native WhatsApp poll vote arrives as
    ``"[poll:<hex>,<hex>]"`` (sha256 digests of the chosen option names — see
    ``whatsapp_web``) and is matched back to labels; a numbered reply ("2", or "1,3" for
    multi-select) selects by position; a button/list tap arrives as ``"[Selected: X]"``
    and matches by label. Multiple picks join with ", ". Anything else — and every
    free-text/secret answer — passes through verbatim, so a user can always type instead.
    """
    raw = text.strip()
    if pending.options:
        if raw.startswith("[poll:") and raw.endswith("]"):
            chosen = {h for h in raw[len("[poll:") : -1].split(",") if h}
            picked = [
                opt for opt in pending.options if hashlib.sha256(opt.encode()).hexdigest() in chosen
            ]
            if picked:
                return ", ".join(picked)
        if raw.startswith("[Selected:") and raw.endswith("]"):
            label = raw[len("[Selected:") : -1].strip().casefold()
            for opt in pending.options:
                if opt.strip().casefold() == label:
                    return opt
        nums = [p.strip() for p in raw.split(",")]
        if nums and all(p.isdigit() for p in nums):
            picked = [
                pending.options[int(p) - 1] for p in nums if 1 <= int(p) <= len(pending.options)
            ]
            if picked:
                return ", ".join(picked)
    return raw
