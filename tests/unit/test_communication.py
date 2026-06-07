"""Unit tests for communication styles (inline voice injection)."""

from __future__ import annotations

import logging

import pytest

from godpy.communication import (
    CAVEMAN_PROMPT,
    HUMAN_PROMPT,
    apply_communication_style,
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
