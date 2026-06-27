"""`gaia style` writes the voice; `gaia tools` surfaces serve without `--all`."""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from gaia import constants
from gaia.cli import app

runner = CliRunner()


def test_gaia_style_sets_and_shows() -> None:
    result = runner.invoke(app, ["style", "caveman"])
    assert result.exit_code == 0, result.output
    assert "default_communication_style: caveman" in constants.CONFIG_PATH.read_text()

    shown = runner.invoke(app, ["style"])
    assert shown.exit_code == 0 and "caveman" in shown.output


def test_gaia_style_rejects_unknown() -> None:
    result = runner.invoke(app, ["style", "shakespeare"])
    assert result.exit_code == 1 and "unknown style" in result.output


def test_tools_lists_serve_without_all(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_select(
        _title: str, rows: list[Any], *, marked: list[str]
    ) -> tuple[list[str], list[str]]:
        captured["ids"] = [r[0] for r in rows]
        return [], []  # no-op

    monkeypatch.setattr("gaia.cli._select.select_manage", fake_select)
    result = runner.invoke(app, ["tools"])  # default menu, no --all
    assert result.exit_code == 0, result.output
    assert "serve" in captured["ids"]  # public link sharing is discoverable by default
