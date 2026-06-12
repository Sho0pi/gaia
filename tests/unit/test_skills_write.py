"""``write_skill``: valid SKILL.md folders, ADK round-trip, overwrite refusal."""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.skills import load_skill, skill_id_for, write_skill


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
