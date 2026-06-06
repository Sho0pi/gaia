"""Unit tests for the connector launch policy (pure, no network)."""

from __future__ import annotations

import pytest

from godpy.app import plan_launch
from godpy.config import GodConfig


def _config(**enabled: bool) -> GodConfig:
    return GodConfig.model_validate(
        {"connectors": {name: {"enabled": on} for name, on in enabled.items()}}
    )


def test_nothing_enabled_launches_nothing() -> None:
    assert plan_launch(GodConfig()) == []


def test_background_connectors_selected() -> None:
    config = _config(whatsapp=True, telegram=True)

    assert plan_launch(config) == ["whatsapp", "telegram"]


def test_cli_only_is_allowed() -> None:
    assert plan_launch(_config(cli=True)) == ["cli"]


def test_cli_with_background_is_rejected() -> None:
    config = _config(cli=True, whatsapp=True)

    with pytest.raises(ValueError, match="foreground-exclusive"):
        plan_launch(config)
