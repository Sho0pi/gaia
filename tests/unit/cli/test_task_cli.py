"""``gaia tasks``: list/show against a tmp board via CliRunner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gaia.cli import app as cli_app
from gaia.missions import Task, TaskStore

runner = CliRunner()


@pytest.fixture(autouse=True)
def tasks_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "tasks.db"
    monkeypatch.setattr("gaia.constants.TASKS_DB", path)  # TaskStore() default
    return path


def test_list_empty(tasks_db: Path) -> None:
    result = runner.invoke(cli_app, ["tasks", "list"])
    assert result.exit_code == 0
    assert "no tasks" in result.output


def test_list_json_reflects_db(tasks_db: Path) -> None:
    store = TaskStore(tasks_db)
    store.create(Task(title="research", owner="itay"))
    store.create(Task(title="purchase", owner="grace"))

    result = runner.invoke(cli_app, ["--json", "tasks", "list"])
    tasks = json.loads(result.output)["tasks"]
    assert {t["title"] for t in tasks} == {"research", "purchase"}  # CLI sees all owners


def test_list_filtered_by_user(tasks_db: Path) -> None:
    store = TaskStore(tasks_db)
    store.create(Task(title="mine", owner="itay"))
    store.create(Task(title="hers", owner="grace"))

    result = runner.invoke(cli_app, ["--json", "tasks", "list", "--user", "itay"])
    tasks = json.loads(result.output)["tasks"]
    assert [t["title"] for t in tasks] == ["mine"]


def test_show_one_task(tasks_db: Path) -> None:
    store = TaskStore(tasks_db)
    t = store.create(Task(title="research", owner="itay"))

    result = runner.invoke(cli_app, ["--json", "tasks", "show", t.id])
    assert json.loads(result.output)["title"] == "research"


def test_show_missing(tasks_db: Path) -> None:
    result = runner.invoke(cli_app, ["tasks", "show", "nope"])
    assert result.exit_code == 1
