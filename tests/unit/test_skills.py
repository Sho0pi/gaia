"""Unit tests for skill loading + attachment (ADK loader, no model backend)."""

from __future__ import annotations

from pathlib import Path

import pytest

from godpy.config import GodConfig
from godpy.skills import DEFAULT_SKILLS_DIR, attach_skills, load_skill, resolve_skills_dir


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


def test_load_missing_skill_warns_and_returns_none(tmp_path: Path) -> None:
    with pytest.warns(UserWarning, match="nope"):
        assert load_skill(tmp_path, "nope") is None


def test_attach_skills_appends_in_order(tmp_path: Path) -> None:
    _make_skill(tmp_path, "one", "FIRST BODY")
    _make_skill(tmp_path, "two", "SECOND BODY")

    result = attach_skills("BASE", ["one", "two"], tmp_path)

    assert result.startswith("BASE")
    assert result.index("FIRST BODY") < result.index("SECOND BODY")
    assert "# Skill: one" in result


def test_attach_skills_skips_unknown(tmp_path: Path) -> None:
    _make_skill(tmp_path, "known", "KNOWN BODY")

    with pytest.warns(UserWarning):
        result = attach_skills("BASE", ["known", "ghost"], tmp_path)

    assert "KNOWN BODY" in result
    assert "ghost" not in result


def test_attach_skills_no_ids_returns_base(tmp_path: Path) -> None:
    assert attach_skills("BASE", [], tmp_path) == "BASE"


def test_resolve_skills_dir_default_and_override(tmp_path: Path) -> None:
    assert resolve_skills_dir(GodConfig()) == DEFAULT_SKILLS_DIR
    assert resolve_skills_dir(GodConfig(skills_dir=tmp_path)) == tmp_path
