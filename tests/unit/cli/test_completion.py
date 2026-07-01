"""``gaia completion`` helpers: script removal on uninstall + best-effort rc editing."""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.cli import completion


def test_run_uninstall_removes_every_shell_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for p in completion._script_paths():  # pretend all three shells were installed
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# completion")

    removed = completion.run_uninstall()

    assert len(removed) == 3
    assert not any(p.exists() for p in completion._script_paths())


def test_run_uninstall_strips_only_the_bashrc_source_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    bash_script = tmp_path / ".bash_completions" / "gaia.sh"
    rc = tmp_path / ".bashrc"
    rc.write_text(f"export X=1\nsource '{bash_script}'\nalias y=z\n")

    completion.run_uninstall()

    text = rc.read_text()
    assert "gaia.sh" not in text  # the completion source line is gone
    assert "export X=1" in text and "alias y=z" in text  # everything else preserved


def test_run_uninstall_is_a_noop_when_nothing_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert completion.run_uninstall() == []  # nothing to remove, no error
