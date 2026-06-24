"""Unit tests for the skill library (no model backend): loading + attachment + the ADK
toolset, writing a skill folder, and the skill_author draft parser."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from gaia import constants
from gaia.agents.skill_author import _parse_draft
from gaia.config import GaiaConfig
from gaia.skills import attach_skills, load_skill, resolve_skills_dir, skill_id_for, write_skill


def _make_skill(skills_dir: Path, name: str, body: str, description: str = "A test skill.") -> None:
    """Write a minimal valid SKILL.md (ADK requires dir name == frontmatter name)."""
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"
    )


def test_load_skill_returns_body_and_frontmatter(tmp_path: Path) -> None:
    _make_skill(tmp_path, "caveman", "Talk like caveman. Drop articles.")

    skill = load_skill(tmp_path, "caveman")

    assert skill is not None
    assert skill.frontmatter.name == "caveman"
    assert "Talk like caveman" in skill.instructions


def test_load_missing_skill_warns_and_returns_none(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        assert load_skill(tmp_path, "nope") is None
    assert "nope" in caplog.text


def test_attach_skills_appends_in_order(tmp_path: Path) -> None:
    _make_skill(tmp_path, "one", "FIRST BODY")
    _make_skill(tmp_path, "two", "SECOND BODY")

    result = attach_skills("BASE", ["one", "two"], tmp_path)

    assert result.startswith("BASE")
    assert result.index("FIRST BODY") < result.index("SECOND BODY")
    assert "# Skill: one" in result


def test_attach_skills_skips_unknown(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _make_skill(tmp_path, "known", "KNOWN BODY")

    with caplog.at_level(logging.WARNING):
        result = attach_skills("BASE", ["known", "ghost"], tmp_path)

    assert "KNOWN BODY" in result
    assert "ghost" not in result
    assert "ghost" in caplog.text


def test_attach_skills_no_ids_returns_base(tmp_path: Path) -> None:
    assert attach_skills("BASE", [], tmp_path) == "BASE"


@pytest.mark.realhome  # asserts the real default skills dir (constants), not the tmp home
def test_resolve_skills_dir_default_and_override(tmp_path: Path) -> None:
    assert resolve_skills_dir(GaiaConfig()) == constants.SKILLS_DIR
    assert resolve_skills_dir(GaiaConfig(skills_dir=tmp_path)) == tmp_path


def test_build_skill_toolset_exposes_progressive_tools(tmp_path: Path) -> None:
    import asyncio

    from gaia.skills import build_skill_toolset

    _make_skill(tmp_path, "web-research", "Search before answering.")

    toolset = build_skill_toolset(tmp_path)

    assert toolset is not None
    tool_names = {t.name for t in asyncio.run(toolset.get_tools())}
    assert {"list_skills", "load_skill"} <= tool_names  # discovery + load on demand


def test_build_skill_toolset_none_when_empty_or_missing(tmp_path: Path) -> None:
    from gaia.skills import build_skill_toolset

    assert build_skill_toolset(tmp_path / "missing") is None  # no such dir
    (tmp_path / "empty").mkdir()
    assert build_skill_toolset(tmp_path / "empty") is None  # dir with no skills


def test_build_skill_toolset_skips_malformed_skill(tmp_path: Path) -> None:
    from gaia.skills import build_skill_toolset

    _make_skill(tmp_path, "good", "fine body")
    bad = tmp_path / "bad"  # folder name != frontmatter name → ADK loader rejects it
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nname: different\ndescription: x\n---\n\nbody\n")

    toolset = build_skill_toolset(tmp_path)  # the good one still yields a toolset

    assert toolset is not None


# --- write_skill (was test_skills_write.py) ----------------------------------------------


def test_write_skill_roundtrips_through_adk_loader(tmp_path: Path) -> None:
    folder = write_skill(tmp_path, "Web Research", "Search then fetch.", "Always search first.")

    assert folder == tmp_path / "web-research"  # kebab id == folder == frontmatter name
    skill = load_skill(tmp_path, "web-research")
    assert skill is not None
    assert skill.frontmatter.name == "web-research"
    assert "Always search first." in skill.instructions


def test_write_skill_refuses_overwrite(tmp_path: Path) -> None:
    write_skill(tmp_path, "dup", "d", "body")

    with pytest.raises(FileExistsError):
        write_skill(tmp_path, "dup", "d", "body")


def test_skill_id_for_normalizes() -> None:
    assert skill_id_for("Web Research!!") == "web-research"
    assert skill_id_for("  ") == "skill"


def test_write_skill_cleans_up_on_validation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gaia.skills.load_skill", lambda *a: None)  # force validation failure

    with pytest.raises(ValueError, match="failed ADK validation"):
        write_skill(tmp_path, "broken", "d", "body")
    assert not (tmp_path / "broken").exists()  # no half-skill left behind


# --- skill_author draft parser (was test_skill_author.py) --------------------------------


def test_parse_first_line_description_rest_body() -> None:
    desc, body = _parse_draft(
        "Write tight tweets.\n\nKeep it under 280 chars.", fallback_description="x"
    )
    assert desc == "Write tight tweets."
    assert body == "Keep it under 280 chars."


def test_parse_strips_code_fences() -> None:
    text = "```markdown\nSummarize PDFs.\n\nExtract the key points.\n```"
    desc, body = _parse_draft(text, fallback_description="x")
    assert desc == "Summarize PDFs." and body == "Extract the key points."


def test_parse_strips_leading_heading_hashes() -> None:
    desc, _ = _parse_draft("# A great skill\n\nbody", fallback_description="x")
    assert desc == "A great skill"


def test_parse_empty_uses_fallback() -> None:
    desc, _ = _parse_draft("   \n  ", fallback_description="fallback")
    assert desc == "fallback"
