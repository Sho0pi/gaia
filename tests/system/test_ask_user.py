"""System test: ask_user pauses a real run and resumes it with the user's reply.

Drives Gaia's actual root handler against a live model: turn one nudges the model to
elicit a choice (it calls the long-running ask_user tool, so the turn pauses and a
Question is surfaced); turn two answers it and the run resumes with that answer. Gated
on a Gemini key so CI stays green without secrets.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest

from gaia.config import Settings
from gaia.connectors.base import Inbound, Question
from gaia.core import Gaia
from gaia.core.handler import build_handler

pytestmark = [
    pytest.mark.system,
    pytest.mark.skipif(
        not os.environ.get("GEMINI_API_KEY"),
        reason="needs a Gemini key (set GEMINI_API_KEY in .env)",
    ),
]


def test_ask_user_pauses_then_resumes(tmp_path: Path) -> None:
    settings = Settings(agent_registry_dir=tmp_path, config_path=tmp_path / "gaia.yaml")
    (tmp_path / "gaia.yaml").write_text(
        f"llm:\n  model: {settings.model}\nmemory:\n  enabled: false\n"
    )
    gaia = Gaia(settings)
    handler = build_handler(gaia)

    sent: list[Any] = []

    async def send(reply: Any) -> None:
        sent.append(reply)

    async def drive() -> None:
        await handler(
            Inbound(
                text="I can't decide on a fruit. Use the ask_user tool to ask me to choose "
                "between apple, banana, and cherry — offer those three as options and do not "
                "pick for me."
            ),
            send,
        )
        # The model called the long-running tool: the run paused and a Question went out.
        assert handler._pending is not None, "expected the run to pause on ask_user"
        questions = [r for r in sent if isinstance(r, Question)]
        assert questions, "expected a Question to be surfaced"
        assert len(questions[-1].options) == 3

        sent.clear()
        await handler(Inbound(text="2"), send)  # pick the second option (banana)

    try:
        asyncio.run(drive())
    finally:
        asyncio.run(gaia.close())

    # The answer resumed the same run: pending cleared and the model replied in text.
    assert handler._pending is None
    assert any(isinstance(r, str) and r.strip() for r in sent), "expected a text reply after resume"
