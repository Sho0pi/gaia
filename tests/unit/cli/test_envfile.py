"""The 0600 env-file writer: create, update-in-place, append, preserve."""

from __future__ import annotations

import stat
from pathlib import Path

from gaia.cli._envfile import get_env_var, set_env_var


def test_creates_with_0600(tmp_path: Path) -> None:
    path = tmp_path / "home" / ".env"

    set_env_var(path, "GAIA_TELEGRAM_BOT_TOKEN", "abc")

    assert path.read_text() == "GAIA_TELEGRAM_BOT_TOKEN=abc\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_updates_in_place_no_duplicates(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("A=1\nGAIA_TELEGRAM_BOT_TOKEN=old\nB=2\n")

    set_env_var(path, "GAIA_TELEGRAM_BOT_TOKEN", "new")

    text = path.read_text()
    assert text == "A=1\nGAIA_TELEGRAM_BOT_TOKEN=new\nB=2\n"  # unrelated lines untouched
    assert text.count("GAIA_TELEGRAM_BOT_TOKEN") == 1


def test_appends_new_key_preserving_comments(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("# my secrets\nGEMINI_API_KEY=g\n")

    set_env_var(path, "NEW_KEY", "v")

    assert path.read_text() == "# my secrets\nGEMINI_API_KEY=g\nNEW_KEY=v\n"


def test_get_env_var(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text('A=1\nQUOTED="va lue"\n')

    assert get_env_var(path, "A") == "1"
    assert get_env_var(path, "QUOTED") == "va lue"
    assert get_env_var(path, "MISSING") is None
    assert get_env_var(tmp_path / "nope", "A") is None
