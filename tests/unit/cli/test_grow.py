"""``gaia grow run``: autonomous / dry-run / interactive (-i) modes.

Offline: the analyst + apply are faked by monkeypatching ``gaia.analysis.loop`` and
``gaia.analysis.apply``; a tmp ``gaia.yaml`` (memory off) keeps ``Gaia`` construction
light and key-free.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import gaia.analysis.apply as apply_mod
import gaia.analysis.loop as loop_mod
from gaia.analysis import AnalysisReport, MemoryProposal, SkillProposal
from gaia.cli import app as cli_app
from gaia.config import Settings

runner = CliRunner()


@pytest.fixture
def offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Wire get_settings to a tmp home with memory off (no model, no network)."""
    config = tmp_path / "gaia.yaml"
    config.write_text("memory:\n  enabled: false\n")
    settings = Settings(agent_registry_dir=tmp_path, config_path=config, log_dir=tmp_path / "logs")
    monkeypatch.setattr("gaia.config.get_settings", lambda env_file=None: settings)
    return tmp_path


def _report() -> AnalysisReport:
    return AnalysisReport(
        summary="Heavy search usage.",
        skills=[
            SkillProposal(
                name="Web Research",
                description="Search before answering.",
                instructions="Always run web_search first.",
                rationale="web_search called 3 times",
            )
        ],
        memories=[
            MemoryProposal(user_id="itay", fact="Itay relies on web search.", rationale="3 calls")
        ],
    )


def test_autonomous_run_applies_cycle(offline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_cycle(gaia: object) -> list[str]:
        return ["created skill web-research"]

    monkeypatch.setattr(loop_mod, "run_cycle", fake_run_cycle)

    result = runner.invoke(cli_app, ["grow", "run"])

    assert result.exit_code == 0, result.output
    assert "created skill web-research" in result.output
    assert "applied 1 change" in result.output


def test_dry_run_prints_proposals_without_applying(
    offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_analyze(gaia: object) -> tuple[AnalysisReport, str | None]:
        return _report(), "itay"

    applied: list[object] = []
    monkeypatch.setattr(loop_mod, "analyze", fake_analyze)
    monkeypatch.setattr(apply_mod, "apply_report", lambda *a, **k: applied.append(a))

    result = runner.invoke(cli_app, ["grow", "run", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Web Research" in result.output
    assert "dry run" in result.output
    assert not applied  # nothing applied in dry-run


def test_interactive_applies_only_approved(offline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_analyze(gaia: object) -> tuple[AnalysisReport, str | None]:
        return _report(), "itay"

    captured: list[AnalysisReport] = []

    async def fake_apply(
        gaia: object, report: AnalysisReport, *, user_id: str | None = None
    ) -> list[str]:
        captured.append(report)
        return ["created skill web-research"]

    monkeypatch.setattr(loop_mod, "analyze", fake_analyze)
    monkeypatch.setattr(apply_mod, "apply_report", fake_apply)

    # Two prompts (1 skill, 1 memory): approve the skill, decline the memory.
    result = runner.invoke(cli_app, ["grow", "run", "-i"], input="y\nn\n")

    assert result.exit_code == 0, result.output
    assert len(captured) == 1
    assert len(captured[0].skills) == 1  # approved
    assert len(captured[0].memories) == 0  # declined


def test_interactive_decline_all_applies_nothing(
    offline: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_analyze(gaia: object) -> tuple[AnalysisReport, str | None]:
        return _report(), "itay"

    applied: list[object] = []
    monkeypatch.setattr(loop_mod, "analyze", fake_analyze)
    monkeypatch.setattr(apply_mod, "apply_report", lambda *a, **k: applied.append(a))

    result = runner.invoke(cli_app, ["grow", "run", "-i"], input="n\nn\n")

    assert result.exit_code == 0, result.output
    assert "nothing approved" in result.output
    assert not applied


def test_nothing_to_analyze(offline: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_analyze(gaia: object) -> tuple[None, None]:
        return None, None

    monkeypatch.setattr(loop_mod, "analyze", fake_analyze)

    result = runner.invoke(cli_app, ["grow", "run", "--dry-run"])

    assert result.exit_code == 0
    assert "nothing to analyze" in result.output
