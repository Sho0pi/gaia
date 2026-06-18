"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import load_dotenv

from gaia import constants
from gaia.agents import AgentSpec, SoulRegistry

# Make the home .env (GEMINI_API_KEY / GEMINI_MODEL) visible to tests and skip-guards.
load_dotenv(constants.ENV_FILE)


@pytest.fixture(autouse=True)
def _isolate_user_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a test write the real ``~/.gaia/users.json``.

    ``Settings.users_file`` and ``UserStore()`` both read ``constants.USERS_FILE`` at
    construction, so redirecting it to a per-test tmp file isolates every store a test
    builds (directly or via ``Gaia``). Guards against test users leaking into real data.
    """
    monkeypatch.setattr(constants, "USERS_FILE", tmp_path / "users.json")


@pytest.fixture
def registry(tmp_path: Path) -> SoulRegistry:
    return SoulRegistry(tmp_path / "agent_registry")


@pytest.fixture
def sample_spec() -> AgentSpec:
    return AgentSpec(
        name="Email Summarizer",
        description="Summarizes long email threads into bullet points.",
        instruction="Summarize the given email thread concisely.",
        model="gemini-2.0-flash",
        skills=["summarization", "email"],
    )
