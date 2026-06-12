"""``gaia analyze``: digest mode, HITL approval flows, skill/memory writes.

Offline: events seeded into a tmp log dir; the analyst LLM is faked by monkeypatching
``_run_analyst_sync``; memory writes are captured by faking ``_save_memory``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gaia.analysis import AnalysisReport, MemoryProposal, SkillProposal
from gaia.cli import analyze as analyze_mod
from gaia.cli import app as cli_app
from gaia.config import Settings

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Tmp log dir + skills dir wired through get_settings / gaia.yaml."""
    logs = tmp_path / "logs"
    logs.mkdir()
    config = tmp_path / "gaia.yaml"
    config.write_text(f"skills_dir: {tmp_path / 'skills'}\n")
    settings = Settings(log_dir=logs, config_path=config)
    monkeypatch.setattr("gaia.config.get_settings", lambda env_file=None: settings)
    _seed_events(logs / "events.jsonl")
    return tmp_path


def _seed_events(path: Path) -> None:
    now = datetime.now()
    lines = []
    for i in range(3):
        ts = (now - timedelta(minutes=30 - i)).strftime("%Y-%m-%d %H:%M:%S,000")
        lines.append(json.dumps({"asctime": ts, "message": "message_in", "user": "itay"}))
        lines.append(
            json.dumps(
                {"asctime": ts, "message": "tool_used", "tool": "web_search", "status": "success"}
            )
        )
    path.write_text("\n".join(lines) + "\n")


def _report(**over: object) -> AnalysisReport:
    base: dict[str, object] = {
        "summary": "Heavy search usage.",
        "skills": [
            SkillProposal(
                name="Web Research",
                description="Search before answering.",
                instructions="Always run web_search first.",
                rationale="web_search called 3 times",
            )
        ],
        "memories": [
            MemoryProposal(user_id="itay", fact="Itay relies on web search.", rationale="3 calls")
        ],
    }
    return AnalysisReport.model_validate({**base, **over})


def test_no_events_exits_1(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (home / "logs" / "events.jsonl").unlink()

    result = runner.invoke(cli_app, ["analyze"])

    assert result.exit_code == 1
    assert "no events" in result.output


def test_json_mode_emits_digest_without_model_call(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[str] = []
    monkeypatch.setattr(analyze_mod, "_run_analyst_sync", lambda *a: called.append("x"))

    result = runner.invoke(cli_app, ["--json", "analyze"])

    assert result.exit_code == 0
    digest = json.loads(result.output)
    assert digest["users"] == {"itay": 3}
    assert not called  # offline mode never touches the analyst


def test_skill_approved_is_written_and_loads(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analyze_mod, "_run_analyst_sync", lambda *a: _report(memories=[]))

    result = runner.invoke(cli_app, ["analyze"], input="y\n")

    assert result.exit_code == 0, result.output
    skill_md = home / "skills" / "web-research" / "SKILL.md"
    assert skill_md.exists()
    assert "Always run web_search first." in skill_md.read_text()
    assert "1 skill(s) written" in result.output


def test_skill_declined_writes_nothing(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analyze_mod, "_run_analyst_sync", lambda *a: _report(memories=[]))

    result = runner.invoke(cli_app, ["analyze"], input="n\n")

    assert result.exit_code == 0
    assert not (home / "skills").exists() or not list((home / "skills").iterdir())
    assert "1 declined" in result.output


def test_memory_approved_calls_save(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analyze_mod, "_run_analyst_sync", lambda *a: _report(skills=[]))
    saved: list[tuple[str, str]] = []
    monkeypatch.setattr(
        analyze_mod, "_save_memory", lambda ctx, user, fact: saved.append((user, fact))
    )

    result = runner.invoke(cli_app, ["analyze"], input="y\n")

    assert result.exit_code == 0, result.output
    assert saved == [("itay", "Itay relies on web search.")]


def test_yes_approves_everything(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analyze_mod, "_run_analyst_sync", lambda *a: _report())
    saved: list[tuple[str, str]] = []
    monkeypatch.setattr(
        analyze_mod, "_save_memory", lambda ctx, user, fact: saved.append((user, fact))
    )

    result = runner.invoke(cli_app, ["analyze", "--yes"])

    assert result.exit_code == 0, result.output
    assert (home / "skills" / "web-research").exists()
    assert len(saved) == 1
    assert "1 skill(s) written, 1 memory(ies) saved" in result.output


def test_analyst_failure_exits_1(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object) -> AnalysisReport:
        raise RuntimeError("no key")

    monkeypatch.setattr(analyze_mod, "_run_analyst_sync", boom)

    result = runner.invoke(cli_app, ["analyze"])

    assert result.exit_code == 1
    assert "analyst failed" in result.output
