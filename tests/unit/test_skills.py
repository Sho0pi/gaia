"""Unit tests for skill loading + attachment (ADK loader, no model backend)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from gaia import constants
from gaia.config import GaiaConfig
from gaia.skills import attach_skills, load_skill, resolve_skills_dir


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
