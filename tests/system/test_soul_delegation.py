"""System test: a soul writes a real deliverable into its workspace, end to end.

Skipped unless a Gemini key is configured, so CI stays green without secrets.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from godpy import constants
from godpy.agents import AgentSpec
from godpy.config import Settings
from godpy.god import God
from godpy.souls.delegate import _run_soul

pytestmark = pytest.mark.skipif(
    not os.environ.get("GEMINI_API_KEY"),
    reason="needs a Gemini key (set GEMINI_API_KEY in .env)",
)


def _god(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> God:
    # fs tools bind to constants.AGENTS_DIR at God() build, so patch it first.
    monkeypatch.setattr(constants, "AGENTS_DIR", tmp_path / "agents")
    config_path = tmp_path / "god.yaml"
    config_path.write_text("memory:\n  enabled: false\n")
    return God(Settings(agent_registry_dir=tmp_path / "reg", config_path=config_path))


async def test_soul_writes_html_into_its_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    god = _god(tmp_path, monkeypatch)
    spec = AgentSpec(
        name="Web Designer",
        description="Builds small static websites.",
        instruction=(
            "You build websites. Use the fs_write tool to write the requested files into your "
            "workspace. Always actually write the files; do not just describe them."
        ),
        model=god.settings.model,
    )
    soul = god.factory.create_or_reuse(spec)

    summary = await _run_soul(
        god, soul, spec.key, "Create index.html containing an <h1>Hello</h1>.", "tester"
    )

    workspace = tmp_path / "agents" / "web_designer" / "workspace"
    html = list(workspace.rglob("*.html"))
    assert html, f"soul wrote no .html (summary: {summary!r})"
    assert "<h1>" in html[0].read_text().lower() or "hello" in html[0].read_text().lower()


# The full God→soul-smith→soul loop is a 3+ LLM-call flow — exercised as a manual demo
# (see the PR), not an automated test, to avoid free-tier rate-limit flakiness in CI.
