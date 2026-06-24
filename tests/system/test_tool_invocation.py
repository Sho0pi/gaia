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
import shutil
from pathlib import Path
from typing import Any

import pytest

from gaia import constants
from gaia.connectors.base import Inbound
from gaia.core.handler import build_handler
from gaia.missions import TaskStore

#: Codex model id for the ChatGPT path (valid: gpt-5.5 / gpt-5.4* / chat-latest — not gpt-5).
_CHATGPT_MODEL = "gpt-5.4-mini"


def _real_chatgpt_token() -> Path | None:
    """The operator's Sign-in-with-ChatGPT token in the REAL home (read, never written)."""
    token = Path.home() / f".{constants.APP_NAME}" / "openai_chatgpt.json"
    return token if token.exists() else None


# Force the ChatGPT path with GAIA_TEST_MODEL=chatgpt — needed because conftest's load_dotenv
# repopulates GEMINI_API_KEY from ~/.gaia/.env, so unsetting it in the shell won't stick.
_FORCE_CHATGPT = os.environ.get("GAIA_TEST_MODEL", "").lower() == "chatgpt"
_HAS_GEMINI = bool(os.environ.get("GEMINI_API_KEY")) and not _FORCE_CHATGPT
_HAS_MODEL = _HAS_GEMINI or _real_chatgpt_token() is not None

pytestmark = [
    pytest.mark.system,
    pytest.mark.skipif(
        not _HAS_MODEL,
        reason="needs GEMINI_API_KEY or a Sign-in-with-ChatGPT token "
        "(set GAIA_TEST_MODEL=chatgpt to force the ChatGPT path)",
    ),
]


@pytest.fixture
def live_gaia(make_gaia: Any) -> Any:
    """A Gaia on whatever live auth is present: the Gemini key by default, else (or with
    GAIA_TEST_MODEL=chatgpt) the local Sign-in-with-ChatGPT token — copied into the ISOLATED tmp
    home, so the real ~/.gaia is untouched.
    """
    if _HAS_GEMINI:
        return make_gaia()
    token = _real_chatgpt_token()
    if token is None:
        pytest.skip("no Sign-in-with-ChatGPT token (run: uv run gaia llm auth openai)")
    dest = constants.HOME_DIR / "openai_chatgpt.json"  # HOME_DIR is the tmp home here
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(token, dest)
    return make_gaia(
        f"llm:\n  provider: openai-chatgpt\n  model: {_CHATGPT_MODEL}\nmemory:\n  enabled: false\n"
    )


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


async def test_model_invokes_fs_write(live_gaia: Any) -> None:
    await _run_turn(
        live_gaia, "Create a file named ping.txt in your workspace containing exactly: PONG"
    )
    written = constants.AGENTS_DIR / "gaia" / "workspace" / "ping.txt"
    assert written.exists(), "model did not call fs_write to create the file"
    assert "PONG" in written.read_text()


async def test_model_invokes_task_create(live_gaia: Any) -> None:
    await _run_turn(live_gaia, "Add a task to my task board titled 'buy oat milk'.")
    titles = [t.title.lower() for t in TaskStore().list()]  # reads the isolated TASKS_DB
    assert any("milk" in title for title in titles), f"no task filed; board has {titles}"


async def test_model_invokes_exec(live_gaia: Any) -> None:
    replies = await _run_turn(live_gaia, "Run the shell command: echo gaia-pong-42")
    assert any("gaia-pong-42" in r for r in replies), f"command output not relayed: {replies}"
