"""Shared fixtures."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv

from gaia import constants
from gaia.agents import AgentSpec, SoulRegistry

# Make the home .env (GEMINI_API_KEY / GEMINI_MODEL) visible to tests and skip-guards.
load_dotenv(constants.ENV_FILE)

# Auto-accept the first-run usage terms in tests (the CLI's persistent pre-run gate, #251) — the
# CliRunner is non-interactive, so without this every CLI/daemon test would refuse. Harmless: it
# records into each test's isolated tmp home.
os.environ.setdefault("GAIA_ACCEPT_TERMS", "1")


#: HOME_DIR-derived path constants to redirect into a per-test tmp home. Stores/Settings read
#: these at construction/call time, so patching them isolates every store a test builds (directly
#: or via ``Gaia``) — tasks.db, mem0's chroma, the whatsapp session db, users.json, logs, agents.
_HOME_PATHS = {
    "ENV_FILE": ".env",  # secrets — a test that writes one must never touch the real ~/.gaia/.env
    "USERS_FILE": "users.json",
    "TASKS_DB": "tasks.db",
    "SESSION_DB": "whatsapp.db",
    "CRON_FILE": "cron.json",
    "CONFIG_PATH": "gaia.yaml",
    "LOG_DIR": "logs",
    "CRASHES_DIR": "crashes",
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


#: Codex model id for the ChatGPT path (valid: gpt-5.5 / gpt-5.4* / chat-latest — not gpt-5).
_CHATGPT_TEST_MODEL = "gpt-5.4-mini"


@pytest.fixture(autouse=True)
def _route_to_chatgpt(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, _isolate_home: None
) -> None:
    """`GAIA_TEST_MODEL=chatgpt` → run the live (model) system tests through the local
    Sign-in-with-ChatGPT backend instead of Gemini, so they can be exercised live without a
    Gemini key. Copies the real OAuth token into the ISOLATED tmp home (real ~/.gaia untouched)
    and reroutes ``resolve_model`` at every import site to ``ChatGptOAuthLlm(gpt-5.4-mini)``.

    No-op unless the env var is set; only touches ``system``-marked tests (the model-driven ones).
    """
    if os.environ.get("GAIA_TEST_MODEL", "").lower() != "chatgpt":
        return
    if request.node.get_closest_marker("system") is None:
        return
    token = Path.home() / f".{constants.APP_NAME}" / "openai_chatgpt.json"
    if not token.exists():
        pytest.skip("GAIA_TEST_MODEL=chatgpt but no token (run: uv run gaia model)")
    dest = (
        constants.HOME_DIR / "openai_chatgpt.json"
    )  # HOME_DIR is the tmp home (see _isolate_home)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(token, dest)

    from gaia.providers.openai import ChatGptOAuthLlm

    def _chatgpt(*_args: Any, **_kwargs: Any) -> Any:
        return ChatGptOAuthLlm(model=_CHATGPT_TEST_MODEL)

    # The three sites agents resolve their model through (root agent, soul factory, smith).
    for target in (
        "gaia.models.resolve_model",
        "gaia.core.agent.resolve_model",
        "gaia.agents.factory.resolve_model",
    ):
        monkeypatch.setattr(target, _chatgpt, raising=False)


@pytest.fixture
async def make_gaia(tmp_path: Path) -> Any:
    """Factory for an isolated ``Gaia`` that is **closed on teardown**.

    ``make_gaia()`` (optionally with a ``gaia.yaml`` ``config`` string) builds a real Gaia on the
    tmp home (`_isolate_home`); every instance is ``await gaia.close()``d when the test ends, so
    the heavier stateful tests (and the system suite) don't leak tool managers / mcp children or
    re-roll the build + teardown by hand. Memory defaults off (no model key needed to construct).
    """
    from gaia.config import Settings
    from gaia.core import Gaia

    built: list[Gaia] = []

    def _make(config: str = "memory:\n  enabled: false\n") -> Gaia:
        cfg = tmp_path / f"gaia-{len(built)}.yaml"
        cfg.write_text(config)
        gaia = Gaia(Settings(config_path=cfg, agent_registry_dir=tmp_path / f"reg-{len(built)}"))
        built.append(gaia)
        return gaia

    yield _make
    for gaia in built:
        await gaia.close()


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
