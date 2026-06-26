"""``gaia user`` group: list/show/role/name/link/rm against a tmp user store via CliRunner.

Offline — ``UserStore`` defaults to the tmp-home ``users.json`` (the autouse home isolation).
Dummy values only.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from gaia.cli import app as cli_app
from gaia.users import UserStore

runner = CliRunner()


def _seed() -> UserStore:
    store = UserStore()
    store.register("cli", "123", "Itay", "admin")
    store.register("whatsapp", "972@s.whatsapp.net", "Grace", "guest")
    return store


def test_list_empty() -> None:
    result = runner.invoke(cli_app, ["user", "list"])
    assert result.exit_code == 0
    assert "no users" in result.output


def test_list_json() -> None:
    _seed()
    result = runner.invoke(cli_app, ["--json", "user", "list"])
    assert result.exit_code == 0
    ids = {u["id"] for u in json.loads(result.output)["users"]}
    assert ids == {"itay", "grace"}


def test_show_by_identity() -> None:
    _seed()
    result = runner.invoke(cli_app, ["--json", "user", "show", "whatsapp:972@s.whatsapp.net"])
    assert result.exit_code == 0
    assert json.loads(result.output)["id"] == "grace"


def test_show_unknown_exits_1() -> None:
    result = runner.invoke(cli_app, ["user", "show", "nobody"])
    assert result.exit_code == 1


def test_role_change_persists() -> None:
    _seed()
    result = runner.invoke(cli_app, ["user", "role", "grace", "user"])
    assert result.exit_code == 0
    assert UserStore().get("grace").role == "user"  # type: ignore[union-attr]


def test_role_invalid_exits_2() -> None:
    _seed()
    assert runner.invoke(cli_app, ["user", "role", "grace", "superuser"]).exit_code == 2


def test_name_and_link() -> None:
    _seed()
    assert runner.invoke(cli_app, ["user", "name", "grace", "Grace H"]).exit_code == 0
    assert UserStore().get("grace").name == "Grace H"  # type: ignore[union-attr]
    assert runner.invoke(cli_app, ["user", "link", "grace", "telegram:55"]).exit_code == 0
    assert "telegram:55" in UserStore().get("grace").identities  # type: ignore[union-attr]


def test_rm_with_yes() -> None:
    _seed()
    result = runner.invoke(cli_app, ["user", "rm", "grace", "--yes"])
    assert result.exit_code == 0
    assert UserStore().get("grace") is None
