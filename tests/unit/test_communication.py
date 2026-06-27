"""Unit tests for communication styles (inline voice injection)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from gaia.communication import (
    CAVEMAN_PROMPT,
    HUMAN_PROMPT,
    STYLES,
    apply_communication_style,
    current_style,
    set_style,
)


def test_human_prepends_prompt() -> None:
    result = apply_communication_style("BASE", "human")

    assert result.startswith(HUMAN_PROMPT)
    assert result.endswith("BASE")


def test_caveman_prepends_prompt() -> None:
    result = apply_communication_style("BASE", "caveman")

    assert CAVEMAN_PROMPT in result
    assert result.endswith("BASE")


def test_ai_injects_nothing() -> None:
    assert apply_communication_style("BASE", "ai") == "BASE"


def test_unknown_style_warns_and_passes_through(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert apply_communication_style("BASE", "klingon") == "BASE"
    assert "unknown communication style" in caplog.text


def test_set_style_writes_config(tmp_path: object) -> None:
    cfg = tmp_path / "gaia.yaml"  # type: ignore[operator]
    set_style(cfg, "caveman")
    assert "default_communication_style: caveman" in cfg.read_text()


def test_set_style_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unknown style"):
        set_style(object(), "shakespeare")  # type: ignore[arg-type]


def test_current_style_falls_back_to_default() -> None:
    assert current_style(SimpleNamespace()) == "human"  # no field set
    assert current_style(SimpleNamespace(default_communication_style="caveman")) == "caveman"


def test_styles_matches_the_voice_table() -> None:
    assert set(STYLES) == {"human", "caveman", "ai"}
