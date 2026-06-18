"""install_skill: local path + git, validation, multi-skill, overwrite guard."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gaia.skills import install_skill, list_skill_ids


def _skill_folder(root: Path, name: str, body: str = "Do the thing.") -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A {name} skill.\n---\n\n{body}\n"
    )
    return folder


def test_install_single_local_folder(tmp_path: Path) -> None:
    src = _skill_folder(tmp_path / "src", "summarize")
    skills = tmp_path / "skills"
    assert install_skill(skills, str(src)) == ["summarize"]
    assert (skills / "summarize" / "SKILL.md").is_file()


def test_install_folder_of_skills(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _skill_folder(pack, "alpha")
    _skill_folder(pack, "beta")
    skills = tmp_path / "skills"
    assert sorted(install_skill(skills, str(pack))) == ["alpha", "beta"]


def test_install_rename_single(tmp_path: Path) -> None:
    src = _skill_folder(tmp_path / "src", "summarize")
    skills = tmp_path / "skills"
    assert install_skill(skills, str(src), name="My Summary!") == ["my-summary"]


def test_install_refuses_overwrite_then_force(tmp_path: Path) -> None:
    src = _skill_folder(tmp_path / "src", "summarize")
    skills = tmp_path / "skills"
    install_skill(skills, str(src))
    with pytest.raises(FileExistsError):
        install_skill(skills, str(src))
    assert install_skill(skills, str(src), force=True) == ["summarize"]


def test_install_rejects_malformed(tmp_path: Path) -> None:
    # Folder name != frontmatter name -> ADK validation fails -> removed.
    bad = tmp_path / "src" / "mismatch"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\nname: other\ndescription: x\n---\n\nbody\n")
    skills = tmp_path / "skills"
    with pytest.raises(ValueError):
        install_skill(skills, str(bad))
    assert not (skills / "mismatch").exists()  # cleaned up


def test_install_missing_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        install_skill(tmp_path / "skills", str(tmp_path / "nope"))


def test_install_from_git_file_url(tmp_path: Path) -> None:
    # A real local git repo served over file:// exercises the clone branch (no network).
    repo = tmp_path / "repo"
    _skill_folder(repo, "fromgit")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
        check=True,
    )
    skills = tmp_path / "skills"
    assert install_skill(skills, f"file://{repo}") == ["fromgit"]
    assert "fromgit" in list_skill_ids(skills)
