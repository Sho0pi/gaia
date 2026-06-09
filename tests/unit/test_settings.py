"""Unit tests for Settings: defaults from constants, env override, --env-file."""

from __future__ import annotations

from pathlib import Path

import pytest

from godpy import constants
from godpy.config import Settings, configure_adk_env, get_settings


def test_openai_key_reads_env_and_configures_adk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    settings = Settings()
    assert settings.openai_api_key == "sk-test"

    configure_adk_env(settings)
    import os

    assert os.environ["OPENAI_API_KEY"] == "sk-test"


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
