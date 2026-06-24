"""System test: a REAL model, on a real Gaia turn, actually invokes a tool and the side
effect lands.

The unit tier already drives every tool against its real backend (fs on tmp files, task_* on a
real sqlite TaskStore, exec on a real shell) and `test_e2e_turn` runs the whole ADK pipeline —
but with a *scripted* FakeLlm choosing the tool call. The piece only a live model can prove is
that Gaia's instruction + the tool schemas actually get the model to route a plain request to
the right tool. Key-gated, so it runs nightly (system-live) and skips on PRs / no key / quota.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from gaia import constants
from gaia.connectors.base import Inbound
from gaia.core.handler import build_handler
from gaia.missions import TaskStore

# Live model needed. With GAIA_TEST_MODEL=chatgpt the conftest `_route_to_chatgpt` fixture runs
# this (and every other live system test) through the local Sign-in-with-ChatGPT backend instead.
pytestmark = [
    pytest.mark.system,
    pytest.mark.skipif(
        not os.environ.get("GEMINI_API_KEY"),
        reason="needs a Gemini key (or GAIA_TEST_MODEL=chatgpt with a ChatGPT token)",
    ),
]


# ponytail: matches the user-facing strings in handler._friendly_error. The handler SWALLOWS a
# 429/503/network error into one of these replies (no exception escapes), so the conftest
# skip-on-exception hook can't see it — we detect the friendly text and skip here instead.
_MODEL_DOWN_REPLIES = ("rate-limited", "model quota", "model is busy", "network hiccup")


async def _run_turn(gaia: Any, text: str) -> list[str]:
    """One real handler turn; collect the replies. Skip (don't fail) on a model outage."""
    replies: list[str] = []

    async def send(reply: Any) -> None:
        replies.append(str(reply))

    await build_handler(gaia)(Inbound(text=text), send)
    if any(any(marker in r.lower() for marker in _MODEL_DOWN_REPLIES) for r in replies):
        pytest.skip(f"model backend unavailable: {replies}")
    return replies


async def test_model_invokes_fs_write(make_gaia: Any) -> None:
    gaia = make_gaia()
    await _run_turn(gaia, "Create a file named ping.txt in your workspace containing exactly: PONG")
    written = constants.AGENTS_DIR / "gaia" / "workspace" / "ping.txt"
    assert written.exists(), "model did not call fs_write to create the file"
    assert "PONG" in written.read_text()


async def test_model_invokes_task_create(make_gaia: Any) -> None:
    gaia = make_gaia()
    await _run_turn(gaia, "Add a task to my task board titled 'buy oat milk'.")
    titles = [t.title.lower() for t in TaskStore().list()]  # reads the isolated TASKS_DB
    assert any("milk" in title for title in titles), f"no task filed; board has {titles}"


async def test_model_invokes_exec(make_gaia: Any) -> None:
    gaia = make_gaia()
    replies = await _run_turn(gaia, "Run the shell command: echo gaia-pong-42")
    assert any("gaia-pong-42" in r for r in replies), f"command output not relayed: {replies}"
