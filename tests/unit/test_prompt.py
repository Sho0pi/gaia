"""The split system prompt: a stable (cacheable) static block + a per-session dynamic tail."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaia.config.schema import GaiaConfig
from gaia.core import prompt


def _settings() -> Any:
    return SimpleNamespace(model="gemini-2.0-flash")


def _static(tmp_path: Path, **cfg: Any) -> str:
    config = GaiaConfig(**cfg)
    return prompt.build_static_instruction(config, _settings(), tmp_path, style="human")


def test_static_block_is_stable_and_carries_no_per_request_data(tmp_path: Path) -> None:
    a = _static(tmp_path)
    b = _static(tmp_path)
    assert a == b  # deterministic -> the provider can cache it across sessions/users
    # It must not bake in per-session data: no timestamp (the memory section may *reference*
    # the <USER_PROFILE> tag, but the actual profile + date live only in the dynamic tail).
    assert "Current date and time" not in a
    assert "# Gaia" in a and "## How you work" in a  # the framework skeleton is there


def test_dynamic_tail_holds_the_date_and_profile() -> None:
    tail = prompt.build_dynamic_instruction("Monday, 2026-07-01 09:00", "likes tea")
    assert "Monday, 2026-07-01 09:00" in tail
    assert "<USER_PROFILE>\nlikes tea\n</USER_PROFILE>" in tail
    # No profile -> just the time, no empty profile block.
    assert "<USER_PROFILE>" not in prompt.build_dynamic_instruction("now", None)


def test_communication_style_lands_in_the_static_block(tmp_path: Path) -> None:
    config = GaiaConfig()
    caveman = prompt.build_static_instruction(config, _settings(), tmp_path, style="caveman")
    human = prompt.build_static_instruction(config, _settings(), tmp_path, style="human")
    assert "caveman" in caveman.lower() and caveman != human
    ai = prompt.build_static_instruction(config, _settings(), tmp_path, style="ai")
    assert "caveman" not in ai.lower()  # 'ai' is the no-injection voice


def test_gaia_md_untouched_template_injects_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    md = tmp_path / "GAIA.md"
    monkeypatch.setattr("gaia.constants.GAIA_MD", md)
    assert prompt.write_default_gaia_md(md) is True and md.exists()
    assert prompt.load_gaia_md() == ""  # comments + headings only -> empty
    assert "GAIA_MD" not in _static(tmp_path)  # not injected into the prompt


def test_gaia_md_real_content_is_injected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    md = tmp_path / "GAIA.md"
    monkeypatch.setattr("gaia.constants.GAIA_MD", md)
    md.write_text("## Persona\nWarm and witty. Call me Itay.\n")
    assert "Warm and witty" in prompt.load_gaia_md()
    static = _static(tmp_path)
    assert "<GAIA_MD>" in static and "Warm and witty" in static


def test_gaia_md_absent_is_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gaia.constants.GAIA_MD", tmp_path / "nope.md")
    assert prompt.load_gaia_md() == ""
