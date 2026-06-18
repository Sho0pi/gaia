"""Self-improve loop: journal, apply (skill/soul/memory), dedupe/additive, revert."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaia.agents import AgentSpec, SoulRegistry
from gaia.analysis.analyst import SkillProposal, SoulProposal
from gaia.analysis.apply import apply_report, revert_improvement
from gaia.analysis.journal import Improvement, ImprovementJournal

# --- schema leniency (real models omit optional fields) ----------------------------------


def test_report_parses_proposal_missing_rationale() -> None:
    from gaia.analysis.analyst import AnalysisReport

    # gpt-5.4-mini omitted memory.rationale — one missing optional field must not abort.
    raw = '{"summary":"x","memories":[{"user_id":"itay","fact":"likes acl"}]}'
    report = AnalysisReport.model_validate_json(raw)
    assert report.memories[0].fact == "likes acl" and report.memories[0].rationale == ""


# --- journal -----------------------------------------------------------------------------


def test_journal_record_list_dedupe_revert(tmp_path: Path) -> None:
    j = ImprovementJournal(tmp_path / "imp.jsonl")
    a = j.record(Improvement(type="skill", target="alpha", action="created"))
    j.record(Improvement(type="soul", target="bob", action="refined"))
    assert {e.target for e in j.entries()} == {"alpha", "bob"}
    assert j.applied_targets("skill") == {"alpha"}
    assert j.mark_reverted(a.id) is True
    assert j.applied_targets("skill") == set()  # reverted no longer counts


# --- registry.update ---------------------------------------------------------------------


def test_registry_update_backs_up_and_persists(tmp_path: Path) -> None:
    reg = SoulRegistry(tmp_path / "reg")
    reg.save(AgentSpec(name="Data Pro", description="old", instruction="old i", model="m"))
    updated = reg.update("data_pro", description="new", instruction="new i")
    assert updated is not None and reg.get("data_pro").description == "new"  # type: ignore[union-attr]
    assert (tmp_path / "reg" / "data_pro.md.bak").is_file()  # prior version backed up


# --- apply -------------------------------------------------------------------------------


def _gaia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    skills = tmp_path / "skills"
    skills.mkdir()
    cfg = SimpleNamespace(skills_dir=skills, llm=SimpleNamespace(model="m"))
    reg = SoulRegistry(tmp_path / "reg")
    return SimpleNamespace(config=cfg, souls=reg, memory_service=None)


async def test_apply_creates_skill_and_soul(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia = _gaia(tmp_path, monkeypatch)

    # Skip the model: the skill author "researches" -> just return text.
    async def _draft(g, n, b):
        return "a skill", "do the thing"

    monkeypatch.setattr("gaia.agents.skill_author.draft_skill_async", _draft)
    journal = ImprovementJournal(tmp_path / "imp.jsonl")
    report = SimpleNamespace(
        summary="x",
        memories=[],
        skills=[
            SkillProposal(name="Tweeter", description="tweets", instructions="", rationale="r")
        ],
        souls=[
            SoulProposal(
                action="create",
                key="",
                name="Data Pro",
                description="d",
                instruction="i",
                rationale="r",
            )
        ],
    )
    applied = await apply_report(gaia, report, journal=journal)  # type: ignore[arg-type]
    kinds = {(i.type, i.action) for i in applied}
    assert ("skill", "created") in kinds and ("soul", "created") in kinds
    assert (tmp_path / "skills" / "tweeter" / "SKILL.md").is_file()
    assert gaia.souls.get("data_pro") is not None


async def test_apply_is_additive_and_deduped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia = _gaia(tmp_path, monkeypatch)

    async def _draft2(g, n, b):
        return "d", "body"

    monkeypatch.setattr("gaia.agents.skill_author.draft_skill_async", _draft2)
    journal = ImprovementJournal(tmp_path / "imp.jsonl")
    prop = SkillProposal(name="Tweeter", description="d", instructions="", rationale="r")
    report = SimpleNamespace(summary="x", memories=[], skills=[prop], souls=[])
    first = await apply_report(gaia, report, journal=journal)  # type: ignore[arg-type]
    second = await apply_report(gaia, report, journal=journal)  # type: ignore[arg-type]
    assert len(first) == 1 and second == []  # second run skips the already-created skill


async def test_apply_refines_existing_soul(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gaia = _gaia(tmp_path, monkeypatch)
    gaia.souls.save(AgentSpec(name="Data Pro", description="old", instruction="old", model="m"))
    journal = ImprovementJournal(tmp_path / "imp.jsonl")
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
                instruction="better i",
                rationale="r",
            )
        ],
    )
    applied = await apply_report(gaia, report, journal=journal)  # type: ignore[arg-type]
    assert applied and applied[0].action == "refined"
    assert gaia.souls.get("data_pro").description == "better"  # type: ignore[union-attr]


# --- revert ------------------------------------------------------------------------------


def test_revert_removes_created_skill(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    (skills / "tweeter").mkdir(parents=True)
    (skills / "tweeter" / "SKILL.md").write_text("---\nname: tweeter\ndescription: d\n---\n\nb\n")
    reg = SoulRegistry(tmp_path / "reg")
    journal = ImprovementJournal(tmp_path / "imp.jsonl")
    imp = journal.record(Improvement(type="skill", target="tweeter", action="created"))
    msg = revert_improvement(imp.id, skills_dir=skills, registry=reg, journal=journal)
    assert "reverted" in msg and not (skills / "tweeter").exists()
    assert journal.get(imp.id).reverted is True  # type: ignore[union-attr]


def test_revert_restores_refined_soul(tmp_path: Path) -> None:
    reg = SoulRegistry(tmp_path / "reg")
    reg.save(AgentSpec(name="Data Pro", description="old", instruction="old", model="m"))
    reg.update("data_pro", description="new")  # leaves a .bak
    journal = ImprovementJournal(tmp_path / "imp.jsonl")
    imp = journal.record(Improvement(type="soul", target="data_pro", action="refined"))
    revert_improvement(imp.id, skills_dir=tmp_path / "skills", registry=reg, journal=journal)
    assert reg.get("data_pro").description == "old"  # type: ignore[union-attr]
