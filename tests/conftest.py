"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import load_dotenv

from godpy.agents import AgentRegistry, AgentSpec

# Make .env (GEMINI_API_KEY / GEMINI_MODEL) visible to tests and the skip-guards.
load_dotenv()


@pytest.fixture
def registry(tmp_path: Path) -> AgentRegistry:
    return AgentRegistry(tmp_path / "agent_registry")


@pytest.fixture
def sample_spec() -> AgentSpec:
    return AgentSpec(
        name="Email Summarizer",
        description="Summarizes long email threads into bullet points.",
        instruction="Summarize the given email thread concisely.",
        model="gemini-2.0-flash",
        skills=["summarization", "email"],
    )
