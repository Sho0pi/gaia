"""System test: a real skill folder is loaded and attached end-to-end (no model)."""

from __future__ import annotations

from pathlib import Path

from godpy.skills import attach_skills

_CAVEMAN_BODY = "Respond terse like smart caveman. Drop articles. Fragments OK."


def test_skill_folder_attaches_to_instruction(tmp_path: Path) -> None:
    skill_dir = tmp_path / "caveman"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: caveman\ndescription: Ultra-compressed mode.\n---\n\n{_CAVEMAN_BODY}\n"
    )

    instruction = attach_skills("You are God.", ["caveman"], tmp_path)

    assert "You are God." in instruction
    assert _CAVEMAN_BODY in instruction
