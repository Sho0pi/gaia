"""``gaia cron``: list/add/rm/enable/edit against a tmp store via CliRunner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gaia.cli import app as cli_app
from gaia.cli import cron as cron_cli
from gaia.cron import CronJob, CronStore

runner = CliRunner()


@pytest.fixture(autouse=True)
def cron_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "cron.json"
    monkeypatch.setattr("gaia.constants.CRON_FILE", path)  # CronStore() default
    return path


def test_add_and_list(cron_file: Path) -> None:
    added = runner.invoke(cli_app, ["cron", "new", "0 9 * * *", "AI news brief", "--name", "news"])
    assert added.exit_code == 0, added.output

    listed = runner.invoke(cli_app, ["--json", "cron", "list"])
    jobs = json.loads(listed.output)["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["expr"] == "0 9 * * *"
    assert jobs[0]["name"] == "news"


def test_add_every_and_at_flags(cron_file: Path) -> None:
    assert runner.invoke(cli_app, ["cron", "new", "--every", "60", "tick"]).exit_code == 0
    assert (
        runner.invoke(cli_app, ["cron", "new", "--at", "2030-01-01T09:00:00", "once"]).exit_code
        == 0
    )

    jobs = CronStore(cron_file).list()
    assert {j.kind for j in jobs} == {"every", "at"}


def test_add_bad_expression_exits_2(cron_file: Path) -> None:
    result = runner.invoke(cli_app, ["cron", "new", "not cron", "x"])

    assert result.exit_code == 2
    assert "invalid cron expression" in result.output


def test_rm_and_unknown(cron_file: Path) -> None:
    job = CronStore(cron_file).add(CronJob(kind="every", expr="60", message="x"))

    assert runner.invoke(cli_app, ["cron", "rm", job.id]).exit_code == 0
    assert runner.invoke(cli_app, ["cron", "rm", job.id]).exit_code == 1  # gone


def test_enable_disable(cron_file: Path) -> None:
    job = CronStore(cron_file).add(CronJob(kind="every", expr="60", message="x"))

    runner.invoke(cli_app, ["cron", "disable", job.id])
    assert CronStore(cron_file).get(job.id).enabled is False  # type: ignore[union-attr]
    runner.invoke(cli_app, ["cron", "enable", job.id])
    assert CronStore(cron_file).get(job.id).enabled is True  # type: ignore[union-attr]


def test_edit_validates_on_save(cron_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = CronStore(cron_file)
    job = store.add(CronJob(kind="cron", expr="0 9 * * *", message="x"))

    # Valid edit: change the schedule.
    edited = json.dumps([{**job.model_dump(), "expr": "30 18 * * 5"}])
    monkeypatch.setattr(cron_cli.click, "edit", lambda *a, **k: edited)
    assert runner.invoke(cli_app, ["cron", "edit"]).exit_code == 0
    assert CronStore(cron_file).get(job.id).expr == "30 18 * * 5"  # type: ignore[union-attr]

    # Invalid edit: rejected, file untouched.
    monkeypatch.setattr(cron_cli.click, "edit", lambda *a, **k: '[{"kind": "cron"}]')
    assert runner.invoke(cli_app, ["cron", "edit"]).exit_code == 1
    assert CronStore(cron_file).get(job.id).expr == "30 18 * * 5"  # type: ignore[union-attr]
