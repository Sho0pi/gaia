"""``gaia acl`` group: list groups + grant/revoke/perms against a tmp user store via CliRunner."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from gaia.cli import app as cli_app
from gaia.users import UserStore

runner = CliRunner()


def _seed() -> None:
    UserStore().register("cli", "1", "Itay", "user")


def test_list_groups() -> None:
    result = runner.invoke(cli_app, ["--json", "acl", "list"])
    assert result.exit_code == 0
    groups = json.loads(result.output)["groups"]
    assert "web" in groups and isinstance(groups["web"], list)


def test_grant_then_perms() -> None:
    _seed()
    assert runner.invoke(cli_app, ["acl", "grant", "itay", "shell"]).exit_code == 0
    assert "shell" in UserStore().get("itay").grants  # type: ignore[union-attr]

    result = runner.invoke(cli_app, ["--json", "acl", "perms", "itay"])
    assert result.exit_code == 0
    assert "shell" in json.loads(result.output)["capabilities"]


def test_revoke() -> None:
    _seed()
    runner.invoke(cli_app, ["acl", "grant", "itay", "shell"])
    assert runner.invoke(cli_app, ["acl", "revoke", "itay", "shell"]).exit_code == 0
    assert "shell" in UserStore().get("itay").denies  # type: ignore[union-attr]


def test_perms_unknown_user_exits_1() -> None:
    assert runner.invoke(cli_app, ["acl", "perms", "nobody"]).exit_code == 1
