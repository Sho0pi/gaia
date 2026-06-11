"""Unit tests for the centralized identity constants."""

from __future__ import annotations

from pathlib import Path

from gaia import constants


def test_home_dir_derives_from_app_name() -> None:
    assert constants.HOME_DIR == Path.home() / f".{constants.APP_NAME}"


def test_env_prefix_derives_from_app_name() -> None:
    assert constants.ENV_PREFIX == f"{constants.APP_NAME.upper()}_"


def test_paths_sit_under_home_dir() -> None:
    for path in (
        constants.ENV_FILE,
        constants.CONFIG_PATH,
        constants.LOG_DIR,
        constants.SKILLS_DIR,
        constants.SESSION_DB,
        constants.AGENT_REGISTRY_DIR,
    ):
        assert path.parent == constants.HOME_DIR


def test_logger_names() -> None:
    assert constants.LOGGER_NAME == constants.APP_NAME
    assert constants.EVENTS_LOGGER_NAME == f"{constants.APP_NAME}.events"
