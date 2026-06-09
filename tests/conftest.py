"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import load_dotenv

from godpy import constants
from godpy.agents import AgentSpec, SoulRegistry

# Make the home .env (GEMINI_API_KEY / GEMINI_MODEL) visible to tests and skip-guards.
load_dotenv(constants.ENV_FILE)


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
