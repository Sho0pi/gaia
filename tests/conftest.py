"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import load_dotenv

from gaia import constants
from gaia.agents import AgentSpec, SoulRegistry

# Make the home .env (GEMINI_API_KEY / GEMINI_MODEL) visible to tests and skip-guards.
load_dotenv(constants.ENV_FILE)


#: HOME_DIR-derived path constants to redirect into a per-test tmp home. Stores/Settings read
#: these at construction/call time, so patching them isolates every store a test builds (directly
#: or via ``Gaia``) — tasks.db, mem0's chroma, the whatsapp session db, users.json, logs, agents.
_HOME_PATHS = {
    "USERS_FILE": "users.json",
    "TASKS_DB": "tasks.db",
    "SESSION_DB": "whatsapp.db",
    "CRON_FILE": "cron.json",
    "CONFIG_PATH": "gaia.yaml",
    "LOG_DIR": "logs",
    "AGENT_REGISTRY_DIR": "agent_registry",
    "AGENTS_DIR": "agents",
    "UPLOADS_DIR": "uploads",
    "CACHE_DIR": "cache",
    "SKILLS_DIR": "skills",
}


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "realhome: opt out of the tmp-home isolation (tests that assert the real paths)"
    )


#: Error signatures that mean "the model backend is unavailable" (no quota, overloaded, bad or
#: expired key) — an *infra* problem, not a test failure. Mirrors ``core/handler._friendly_error``.
_MODEL_UNAVAILABLE = (
    "resource_exhausted",
    "429",
    "rate limit",
    "quota",
    "unavailable",
    "503",
    "overloaded",
    "permission_denied",
    "api key not valid",
    "api_key_invalid",
    "unauthenticated",
    "401",
    "403",
)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> pytest.TestReport:
    """Turn a model-backend outage in a ``system`` test into a SKIP, not a failure.

    So the nightly live tier goes yellow (skipped) when the key is exhausted/overloaded/invalid,
    instead of red — a real assertion failure (a genuine logic break) still fails as normal.
    """
    report = yield
    if (
        report.when == "call"
        and report.failed
        and item.get_closest_marker("system") is not None
        and call.excinfo is not None
    ):
        message = str(call.excinfo.value).lower()
        if any(sign in message for sign in _MODEL_UNAVAILABLE):
            report.outcome = "skipped"
            # The terminal's skip summary expects a (path, lineno, reason) tuple longrepr.
            path, lineno, _ = item.location
            reason = f"Skipped: model backend unavailable — {str(call.excinfo.value)[:160]}"
            report.longrepr = (path, (lineno or 0) + 1, reason)
    return report


@pytest.fixture(autouse=True)
def _isolate_home(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never let a test write the real ``~/.gaia``.

    Several stores default to a ``constants`` path under the operator's home and read it at
    construction (``TaskStore`` → ``TASKS_DB``) or call time (mem0's chroma → ``HOME_DIR``), so a
    test that builds a ``Gaia`` would write live data. Redirect the whole home (and each derived
    path) into a per-test tmp dir. ``Settings`` path fields read these constants via
    ``default_factory`` (see ``config/settings.py``), so a ``Settings()`` built in a test picks up
    the tmp home too. One knob isolates everything; a test may still override a single path, or
    opt out entirely with ``@pytest.mark.realhome`` (the few tests asserting the real wiring).
    """
    if request.node.get_closest_marker("realhome"):
        return
    home = tmp_path / "home"
    monkeypatch.setattr(constants, "HOME_DIR", home)  # mem0 chroma reads this at call time
    for name, leaf in _HOME_PATHS.items():
        monkeypatch.setattr(constants, name, home / leaf)


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
