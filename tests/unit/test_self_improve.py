"""Self-improve loop: git state repo, apply (skill/soul/memory), revert one-of-many."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaia.agents import AgentSpec, SoulRegistry
from gaia.analysis.analyst import SkillProposal, SoulProposal
from gaia.analysis.apply import apply_report
from gaia.state import StateRepo

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git_out(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


# --- schema leniency ---------------------------------------------------------------------


def test_report_parses_proposal_missing_rationale() -> None:
    from gaia.analysis.analyst import AnalysisReport

    raw = '{"summary":"x","memories":[{"user_id":"itay","fact":"likes acl"}]}'
    report = AnalysisReport.model_validate_json(raw)
    assert report.memories[0].fact == "likes acl" and report.memories[0].rationale == ""


# --- state repo --------------------------------------------------------------------------


def test_repo_tracks_artifacts_not_secrets(tmp_path: Path) -> None:
    (tmp_path / "agent_registry").mkdir()
    (tmp_path / "agent_registry" / "x.md").write_text("soul")
    (tmp_path / "skills").mkdir()
    (tmp_path / ".env").write_text("SECRET=1")  # must NOT be tracked
    (tmp_path / "users.json").write_text("[]")

    repo = StateRepo(tmp_path)
    sha = repo.commit("soul: created 'x'", "body")
    assert sha
    tracked = _git_out(tmp_path, "ls-files")
    assert "agent_registry/x.md" in tracked
    assert ".env" not in tracked and "users.json" not in tracked  # allowlist gitignore works


def test_commit_noop_when_clean(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    repo = StateRepo(tmp_path)
    repo.commit("init artifact", "")  # nothing in skills yet -> no-op
    (tmp_path / "skills" / "a").mkdir()
    (tmp_path / "skills" / "a" / "SKILL.md").write_text("x")
    assert repo.commit("skill: created 'a'") is not None
    assert repo.commit("skill: created 'a'") is None  # nothing changed since


def test_entries_and_revert_one_of_many(tmp_path: Path) -> None:
    reg = SoulRegistry(tmp_path / "agent_registry")
    repo = StateRepo(tmp_path)
    reg.save(AgentSpec(name="Alpha", description="a", instruction="ia", model="m"))
    sha_a = repo.commit("improve: created soul 'alpha'")
    reg.save(AgentSpec(name="Beta", description="b", instruction="ib", model="m"))
    repo.commit("improve: created soul 'beta'")

    assert {e.sha for e in repo.entries()} >= {sha_a}
    # Revert only alpha — beta untouched (different file => clean).
    msg = repo.revert(sha_a)
    assert "reverted" in msg
    assert not (tmp_path / "agent_registry" / "alpha.md").exists()
    assert (tmp_path / "agent_registry" / "beta.md").exists()
    assert any(e.reverted for e in repo.entries())


def test_revert_conflict_when_same_soul_changed_twice(tmp_path: Path) -> None:
    reg = SoulRegistry(tmp_path / "agent_registry")
    repo = StateRepo(tmp_path)
    reg.save(AgentSpec(name="Alpha", description="v1", instruction="i", model="m"))
    sha1 = repo.commit("improve: created soul 'alpha'")
    reg.update("alpha", description="v2")
    repo.commit("improve: refined soul 'alpha'")
    msg = repo.revert(sha1)  # later commit touched the same file
    assert "could not revert" in msg


# --- apply -------------------------------------------------------------------------------


def _gaia(tmp_path: Path) -> Any:
    cfg = SimpleNamespace(skills_dir=tmp_path / "skills", llm=SimpleNamespace(model="m"))
    return SimpleNamespace(
        config=cfg, souls=SoulRegistry(tmp_path / "agent_registry"), memory_service=None
    )


async def test_apply_creates_and_commits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gaia.constants.HOME_DIR", tmp_path)
    gaia = _gaia(tmp_path)

    async def _draft(g: Any, n: str, b: str) -> tuple[str, str]:
        return "a skill", "do the thing"

    monkeypatch.setattr("gaia.agents.skill_author.draft_skill_async", _draft)
    report = SimpleNamespace(
        summary="x",
        memories=[],
        skills=[SkillProposal(name="Tweeter", description="tweets", rationale="r")],
        souls=[SoulProposal(action="create", name="Data Pro", description="d", instruction="i")],
    )
    applied = await apply_report(gaia, report)  # type: ignore[arg-type]
    assert any("skill" in a for a in applied) and any("soul" in a for a in applied)
    # both were committed to the state repo
    assert len(StateRepo(tmp_path).entries()) >= 2


async def test_apply_additive_skips_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gaia.constants.HOME_DIR", tmp_path)
    gaia = _gaia(tmp_path)

    async def _draft(g: Any, n: str, b: str) -> tuple[str, str]:
        return "d", "body"

    monkeypatch.setattr("gaia.agents.skill_author.draft_skill_async", _draft)
    report = SimpleNamespace(
        summary="x", memories=[], skills=[SkillProposal(name="Tweeter", description="d")], souls=[]
    )
    assert len(await apply_report(gaia, report)) == 1  # type: ignore[arg-type]
    assert await apply_report(gaia, report) == []  # type: ignore[arg-type]  # already on disk


async def test_apply_refines_existing_soul(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gaia.constants.HOME_DIR", tmp_path)
    gaia = _gaia(tmp_path)
    gaia.souls.save(AgentSpec(name="Data Pro", description="old", instruction="old", model="m"))
    report = SimpleNamespace(
        summary="x",
        memories=[],
        skills=[],
        souls=[
            SoulProposal(
                action="refine",
                key="data_pro",
                name="Data Pro",
                description="better",
                instruction="bi",
            )
        ],
    )
    applied = await apply_report(gaia, report)  # type: ignore[arg-type]
    assert applied == ["refined soul data_pro"]
    assert gaia.souls.get("data_pro").description == "better"  # type: ignore[union-attr]
