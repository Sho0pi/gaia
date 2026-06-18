"""``gaia skill`` group: list / show / new / install / remove (offline, no model key)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from gaia.cli import app as cli_app
from gaia.config import Settings

runner = CliRunner()


@pytest.fixture
def skills_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp skills dir wired through config (skills_dir) + the light get_settings."""
    skills = tmp_path / "skills"
    skills.mkdir()
    (tmp_path / "gaia.yaml").write_text(f"skills_dir: {skills}\n")
    settings = Settings(agent_registry_dir=tmp_path / "reg", config_path=tmp_path / "gaia.yaml")
    monkeypatch.setattr("gaia.config.get_settings", lambda env_file=None: settings)
    return skills


def _make(skills: Path, name: str, desc: str = "A test skill.") -> None:
    folder = skills / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n\nbody\n")


def test_list_empty(skills_dir: Path) -> None:
    res = runner.invoke(cli_app, ["skill", "list"])
    assert res.exit_code == 0 and "no skills" in res.stdout


def test_list_and_show(skills_dir: Path) -> None:
    _make(skills_dir, "caveman", desc="Talk terse.")
    out = runner.invoke(cli_app, ["skill", "list"])
    assert out.exit_code == 0 and "caveman" in out.stdout

    shown = runner.invoke(cli_app, ["skill", "show", "caveman"])
    assert shown.exit_code == 0 and "Talk terse" in shown.stdout and "body" in shown.stdout


def test_show_unknown(skills_dir: Path) -> None:
    res = runner.invoke(cli_app, ["skill", "show", "ghost"])
    assert res.exit_code == 1 and "no skill" in res.stdout


def test_new_from_instruction_file(skills_dir: Path, tmp_path: Path) -> None:
    body = tmp_path / "body.md"
    body.write_text("Use bullet points.")
    res = runner.invoke(
        cli_app,
        ["skill", "new", "Bulleter", "--description", "Bullets.", "--instruction-file", str(body)],
    )
    assert res.exit_code == 0 and "created skill 'bulleter'" in res.stdout
    assert (skills_dir / "bulleter" / "SKILL.md").is_file()


def test_new_requires_a_source(skills_dir: Path) -> None:
    res = runner.invoke(cli_app, ["skill", "new", "Empty"])
    assert res.exit_code == 1 and "provide --from" in res.stdout


def test_install_local(skills_dir: Path, tmp_path: Path) -> None:
    src = tmp_path / "src" / "imported"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text("---\nname: imported\ndescription: x\n---\n\nbody\n")
    res = runner.invoke(cli_app, ["skill", "install", str(src)])
    assert res.exit_code == 0 and "installed: imported" in res.stdout
    assert (skills_dir / "imported").is_dir()


def test_remove(skills_dir: Path) -> None:
    _make(skills_dir, "trash")
    res = runner.invoke(cli_app, ["skill", "remove", "trash", "--yes"])
    assert res.exit_code == 0 and "trash" in res.stdout
    assert not (skills_dir / "trash").exists()
