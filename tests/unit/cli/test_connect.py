"""``gaia connect``: telegram/whatsapp/cli flows, keep-or-replace gates, fallback menu.

Offline: tmp home wired through get_settings + patched constants.ENV_FILE; the QR
pairing seam (``connect._pair``) and the Bot API verify are monkeypatched. CliRunner
drives the non-tty numbered fallback, per the scripted-input rule.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml as pyyaml
from typer.testing import CliRunner

from gaia.cli import app as cli_app
from gaia.cli import connect as connect_mod
from gaia.cli._envfile import get_env_var
from gaia.config import Settings

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Tmp home: env file, config, session db all under tmp; settings wired in."""
    env = tmp_path / ".env"
    settings = Settings(
        config_path=tmp_path / "gaia.yaml",
        whatsapp_session_db=tmp_path / "whatsapp.db",
        telegram_bot_token=None,
    )
    monkeypatch.setattr("gaia.config.get_settings", lambda env_file=None: settings)
    monkeypatch.setattr("gaia.constants.ENV_FILE", env)
    return tmp_path


def _enabled(home: Path, name: str) -> bool:
    data = pyyaml.safe_load((home / "gaia.yaml").read_text())
    return bool(data["connectors"][name]["enabled"])


# --- telegram -------------------------------------------------------------------


def test_telegram_token_flag_no_verify(home: Path) -> None:
    result = runner.invoke(cli_app, ["connect", "telegram", "--token", "123:abc", "--no-verify"])

    assert result.exit_code == 0, result.output
    assert get_env_var(home / ".env", "GAIA_TELEGRAM_BOT_TOKEN") == "123:abc"
    assert _enabled(home, "telegram")
    assert "foreground-exclusive" in result.output  # the cli-exclusivity warning
    assert "gaia start" in result.output


def test_telegram_prompts_for_token(home: Path) -> None:
    result = runner.invoke(cli_app, ["connect", "telegram", "--no-verify"], input="999:tok\n")

    assert result.exit_code == 0, result.output
    assert get_env_var(home / ".env", "GAIA_TELEGRAM_BOT_TOKEN") == "999:tok"
    assert "BotFather" in result.output  # the tutorial showed


def test_telegram_existing_token_keep(home: Path) -> None:
    (home / ".env").write_text("GAIA_TELEGRAM_BOT_TOKEN=old\n")

    result = runner.invoke(cli_app, ["connect", "telegram", "--no-verify"], input="n\n")

    assert result.exit_code == 0, result.output
    assert get_env_var(home / ".env", "GAIA_TELEGRAM_BOT_TOKEN") == "old"  # kept
    assert _enabled(home, "telegram")  # still enabled


def test_telegram_existing_token_replace(home: Path) -> None:
    (home / ".env").write_text("GAIA_TELEGRAM_BOT_TOKEN=old\n")

    result = runner.invoke(cli_app, ["connect", "telegram", "--no-verify"], input="y\nnew:tok\n")

    assert result.exit_code == 0, result.output
    text = (home / ".env").read_text()
    assert get_env_var(home / ".env", "GAIA_TELEGRAM_BOT_TOKEN") == "new:tok"
    assert text.count("GAIA_TELEGRAM_BOT_TOKEN") == 1  # in place, no dup line


def test_telegram_verify_rejects_bad_token(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connect_mod, "_verify_telegram", lambda token: None)

    result = runner.invoke(cli_app, ["connect", "telegram", "--token", "bad"])

    assert result.exit_code == 1
    assert "rejected" in result.output
    assert get_env_var(home / ".env", "GAIA_TELEGRAM_BOT_TOKEN") is None  # not saved


# --- whatsapp -------------------------------------------------------------------


def test_whatsapp_pairs_and_enables(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paired: list[int] = []

    async def fake_pair(session_db: object, timeout_s: int) -> bool:
        paired.append(timeout_s)
        return True

    monkeypatch.setattr(connect_mod, "_pair", fake_pair)

    result = runner.invoke(cli_app, ["connect", "whatsapp", "--timeout", "5"])

    assert result.exit_code == 0, result.output
    assert paired == [5]
    assert _enabled(home, "whatsapp")
    assert "Linked Devices" in result.output  # the tutorial showed


def test_whatsapp_timeout_fails_without_enabling(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_pair(session_db: object, timeout_s: int) -> bool:
        return False

    monkeypatch.setattr(connect_mod, "_pair", fake_pair)

    result = runner.invoke(cli_app, ["connect", "whatsapp"])

    assert result.exit_code == 1
    assert not (home / "gaia.yaml").exists() or not _enabled(home, "whatsapp")


def test_whatsapp_existing_session_keep(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (home / "whatsapp.db").write_text("session")
    called: list[bool] = []

    async def fake_pair(session_db: object, timeout_s: int) -> bool:  # pragma: no cover
        called.append(True)
        return True

    monkeypatch.setattr(connect_mod, "_pair", fake_pair)

    result = runner.invoke(cli_app, ["connect", "whatsapp"], input="n\n")

    assert result.exit_code == 0, result.output
    assert not called  # kept the session — no re-pair
    assert (home / "whatsapp.db").exists()
    assert _enabled(home, "whatsapp")


def test_whatsapp_existing_session_repair_deletes_db(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (home / "whatsapp.db").write_text("session")

    async def fake_pair(session_db: object, timeout_s: int) -> bool:
        assert not (home / "whatsapp.db").exists()  # deleted before re-pairing
        return True

    monkeypatch.setattr(connect_mod, "_pair", fake_pair)

    result = runner.invoke(cli_app, ["connect", "whatsapp"], input="y\n")

    assert result.exit_code == 0, result.output
    assert _enabled(home, "whatsapp")


# --- selection / cli ---------------------------------------------------------------


def test_unknown_connector_exits_2(home: Path) -> None:
    result = runner.invoke(cli_app, ["connect", "discord"])

    assert result.exit_code == 2
    assert "unknown connector" in result.output


def test_cli_connector_is_informational(home: Path) -> None:
    result = runner.invoke(cli_app, ["connect", "cli"])

    assert result.exit_code == 0
    assert "always available" in result.output


def test_bare_invocation_numbered_fallback(home: Path) -> None:
    # CliRunner stdin is not a tty → the numbered fallback menu; pick cli (3).
    result = runner.invoke(cli_app, ["connect"], input="3\n")

    assert result.exit_code == 0, result.output
    assert "1. telegram" in result.output  # the menu rendered
    assert "always available" in result.output  # the picked flow ran


def test_bare_invocation_nothing_selected(home: Path) -> None:
    result = runner.invoke(cli_app, ["connect"], input="\n")

    assert result.exit_code == 0
    assert "nothing selected" in result.output


async def test_textual_picker_toggles_and_submits() -> None:
    # The tty path: drive the minimal Textual picker headlessly — space toggles
    # connectors, enter returns the selected names in display order.
    app = connect_mod.build_picker()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("space")  # toggle telegram (first, highlighted)
        await pilot.press("down")
        await pilot.press("space")  # toggle whatsapp
        await pilot.press("enter")  # confirm
        await pilot.pause()

    assert app.return_value == ["telegram", "whatsapp"]


async def test_textual_picker_empty_submit_returns_nothing() -> None:
    app = connect_mod.build_picker()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")  # confirm with nothing toggled
        await pilot.pause()

    assert app.return_value == []
