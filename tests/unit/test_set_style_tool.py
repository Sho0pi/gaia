"""The `set_communication_style` tool — writes the canonical gaia.yaml, returns a status dict."""

from __future__ import annotations

import pytest

from gaia import constants
from gaia.tools.set_communication_style import make_set_communication_style


async def test_tool_sets_style_and_writes_config() -> None:
    tool = make_set_communication_style()
    result = await tool("CAVEMAN")  # case-insensitive
    assert result["status"] == "success" and result["style"] == "caveman"
    assert "default_communication_style: caveman" in constants.CONFIG_PATH.read_text()


async def test_tool_rejects_unknown_style() -> None:
    result = await make_set_communication_style()("shakespeare")
    assert result["status"] == "error" and "shakespeare" in result["error_message"]
    assert not constants.CONFIG_PATH.exists()  # nothing written on a bad style


@pytest.mark.parametrize("style", ["human", "caveman", "ai"])
async def test_tool_accepts_every_voice(style: str) -> None:
    assert (await make_set_communication_style()(style))["status"] == "success"
