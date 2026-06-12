"""``gaia logs``: tail, file selection, missing-file exit, event rendering, follow.

Everything is offline: a tmp log dir patched into ``get_settings``; the follow loop is
driven deterministically by a fake ``time.sleep`` that mutates the file between polls
(append, then rotate) and finally raises ``KeyboardInterrupt`` to end the loop.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gaia.cli import app as cli_app
from gaia.cli import logs
from gaia.config import Settings

runner = CliRunner()


@pytest.fixture
def log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp log dir wired into the light ``get_settings`` the command calls."""
    d = tmp_path / "logs"
    d.mkdir()
    settings = Settings(log_dir=d)
    monkeypatch.setattr("gaia.config.get_settings", lambda env_file=None: settings)
    return d


# --- tail_lines --------------------------------------------------------------------


def test_tail_lines_returns_exactly_last_n(tmp_path: Path) -> None:
    f = tmp_path / "f.log"
    f.write_text("".join(f"line {i}\n" for i in range(10)))

    assert logs.tail_lines(f, 5) == [f"line {i}" for i in range(5, 10)]


def test_tail_lines_missing_file_is_empty(tmp_path: Path) -> None:
    assert logs.tail_lines(tmp_path / "nope.log", 5) == []


# --- the command -------------------------------------------------------------------


def test_logs_prints_last_n_lines(log_dir: Path) -> None:
    (log_dir / "system.log").write_text("".join(f"line {i}\n" for i in range(20)))

    result = runner.invoke(cli_app, ["logs", "-n", "5"])

    assert result.exit_code == 0
    printed = [ln for ln in result.output.splitlines() if ln]
    assert printed == [f"line {i}" for i in range(15, 20)]


def test_logs_missing_file_exits_1_with_hint(log_dir: Path) -> None:
    result = runner.invoke(cli_app, ["logs"])

    assert result.exit_code == 1
    assert "no log file" in result.output
    assert "gaia chat" in result.output and "gaia start" in result.output


def test_logs_selects_errors_file(log_dir: Path) -> None:
    (log_dir / "errors.log").write_text("a warning\n")

    result = runner.invoke(cli_app, ["logs", "--errors"])

    assert result.exit_code == 0
    assert "a warning" in result.output


def test_logs_rejects_multiple_file_selectors(log_dir: Path) -> None:
    result = runner.invoke(cli_app, ["logs", "--errors", "--events"])

    assert result.exit_code != 0
    assert "at most one" in result.output


def test_events_pretty_vs_raw(log_dir: Path) -> None:
    line = json.dumps(
        {"asctime": "2026-06-12 09:30:00,123", "message": "tool_used", "tool": "web_search"}
    )
    (log_dir / "events.jsonl").write_text(line + "\n")

    pretty = runner.invoke(cli_app, ["logs", "--events"])
    assert pretty.exit_code == 0
    assert "09:30:00 ▸ tool_used" in pretty.output
    assert "tool=web_search" in pretty.output

    raw = runner.invoke(cli_app, ["logs", "--events", "--json"])
    # --json prints the raw event lines: the verbatim JSON survives round-trip
    assert raw.exit_code == 0
    assert '"tool_used"' in raw.output


# --- follow across rotation --------------------------------------------------------


def test_follow_survives_rotation(log_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = log_dir / "system.log"
    path.write_text("old\n")  # already tailed; follow starts at EOF
    captured: list[str] = []
    monkeypatch.setattr(
        logs, "console", lambda: type("C", (), {"print": lambda self, m: captured.append(m)})()
    )

    calls = {"n": 0}

    def fake_sleep(_seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            with path.open("a") as fh:  # append to the live file
                fh.write("appended\n")
        elif calls["n"] == 2:
            path.rename(log_dir / "system.log.1")  # rollover: new inode
            path.write_text("rotated\n")
        else:
            raise KeyboardInterrupt  # end the follow loop

    monkeypatch.setattr(logs.time, "sleep", fake_sleep)

    with pytest.raises(KeyboardInterrupt):
        logs._follow(path, events=False, raw=False)

    assert "appended" in captured  # read before rotation
    assert "rotated" in captured  # picked up the new file after the inode changed
