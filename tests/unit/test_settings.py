"""Unit tests for Settings: defaults from constants, env override, --env-file."""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia import constants
from gaia.config import Settings, configure_adk_env, get_settings


def test_openai_key_reads_env_and_configures_adk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    settings = Settings()
    assert settings.openai_api_key == "sk-test"

    configure_adk_env(settings)
    import os

    assert os.environ["OPENAI_API_KEY"] == "sk-test"


def test_anthropic_and_openrouter_keys_bridge_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    settings = Settings()
    assert settings.anthropic_api_key == "sk-ant-test"
    assert settings.openrouter_api_key == "sk-or-test"

    configure_adk_env(settings)
    import os

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test"
    assert os.environ["OPENROUTER_API_KEY"] == "sk-or-test"


def test_configure_adk_env_disables_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DO_NOT_TRACK", "OTEL_SDK_DISABLED", "ANONYMIZED_TELEMETRY", "MEM0_TELEMETRY"):
        monkeypatch.delenv(name, raising=False)

    configure_adk_env(Settings())
    import os

    assert os.environ["OTEL_SDK_DISABLED"] == "true"
    assert os.environ["ANONYMIZED_TELEMETRY"] == "False"
    assert os.environ["MEM0_TELEMETRY"] == "false"
    assert os.environ["DO_NOT_TRACK"] == "1"


def test_configure_adk_env_telemetry_is_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    # setdefault: an operator who explicitly opts back in wins.
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")

    configure_adk_env(Settings())
    import os

    assert os.environ["OTEL_SDK_DISABLED"] == "false"


def test_defaults_come_from_constants() -> None:
    settings = Settings()

    assert settings.config_path == constants.CONFIG_PATH
    assert settings.log_dir == constants.LOG_DIR
    assert settings.whatsapp_session_db == constants.SESSION_DB
    assert settings.agent_registry_dir == constants.AGENT_REGISTRY_DIR
    assert settings.mem0_collection == constants.APP_NAME


def test_env_var_alias_still_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(f"{constants.ENV_PREFIX}LOG_DIR", str(tmp_path / "custom"))

    assert Settings().log_dir == tmp_path / "custom"


def test_get_settings_reads_supplied_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # os.environ outranks a .env file, so clear the var before reading from the file.
    monkeypatch.delenv(f"{constants.ENV_PREFIX}MEM0_COLLECTION", raising=False)
    env = tmp_path / "dev.env"
    env.write_text(f"{constants.ENV_PREFIX}MEM0_COLLECTION=from-file\n")

    assert get_settings(env_file=env).mem0_collection == "from-file"


def test_arbitrary_dotenv_secret_is_exported(monkeypatch: pytest.MonkeyPatch) -> None:
    # A key gaia doesn't model (e.g. an MCP server's token) must still reach the process env, so
    # env_passthrough / ${VAR} headers can use it. constants.ENV_FILE is the isolated tmp home here.
    constants.ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    constants.ENV_FILE.write_text("TICKTICK_TOKEN=abc123\n# a comment\nEMPTY=\n")
    monkeypatch.delenv("TICKTICK_TOKEN", raising=False)
    import os

    try:
        configure_adk_env(Settings())
        assert os.environ["TICKTICK_TOKEN"] == "abc123"
        assert os.environ.get("EMPTY") == ""
    finally:
        os.environ.pop("TICKTICK_TOKEN", None)
        os.environ.pop("EMPTY", None)
