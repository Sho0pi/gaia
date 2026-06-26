"""Unit tests for the gaia.yaml schema parsing + defaults."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gaia.config import GaiaConfig

# Trimmed version of the prior-art POC from issue #10.
_POC = {
    "llm": {"provider": "gemini", "model": "gemini-3.1-flash-lite"},
    "admin": ["123456789"],
    "connectors": {
        "whatsapp": {
            "enabled": True,
            "group_trigger": {"mention_only": True},
        },
        "cli": {"enabled": True, "default_role": "admin"},
        "telegram": {"enabled": True},
    },
    "logging": {"level": "DEBUG", "max_size_mb": 10},
    "default_communication_style": "human",
    "skills_dir": "/custom/skills",
    "agents": {"gaia": {"communication_style": "caveman", "skills": ["caveman"]}},
    "roles": {"guest": {"capabilities": ["web"]}},
    "tools": {"web_extract": {"enabled": True, "max_chars": 8000}},
    "souls": {"gaia": None},
}


def test_parses_poc_sample() -> None:
    config = GaiaConfig.model_validate(_POC)

    assert config.llm.model == "gemini-3.1-flash-lite"
    assert config.admin == ["123456789"]
    assert config.connectors.whatsapp.enabled is True
    assert config.connectors.whatsapp.group_trigger.mention_only is True
    assert config.connectors.cli.default_role == "admin"
    assert config.logging.level == "DEBUG"
    assert config.logging.max_size_mb == 10
    assert config.logging.backup_count == 5  # default kept
    assert config.default_communication_style == "human"
    assert str(config.skills_dir) == "/custom/skills"
    assert config.agents["gaia"].communication_style == "caveman"
    assert config.agents["gaia"].skills == ["caveman"]
    assert config.roles["guest"].capabilities == ["web"]
    # ToolConfig keeps unknown tool-specific keys verbatim (extra="allow").
    assert config.tools["web_extract"].model_extra == {"max_chars": 8000}


def test_empty_config_is_all_defaults() -> None:
    config = GaiaConfig.model_validate({})

    assert config.llm.model == "gemini-2.0-flash"
    assert config.admin == []
    # Every connector defaults to disabled — an empty file launches nothing.
    assert config.connectors.whatsapp.enabled is False
    assert config.connectors.cli.enabled is False
    assert config.connectors.telegram.enabled is False
    assert config.connectors.whatsapp.group_trigger.mention_only is True
    assert config.default_communication_style == "human"


def test_remote_channels_default_to_guest_local_to_admin() -> None:
    # The access gate hinges on these: a first-seen remote sender must land as 'guest'
    # (gated) by default; the local TUI operator is trusted ('admin').
    config = GaiaConfig.model_validate({})

    assert config.connectors.whatsapp.default_role == "guest"
    assert config.connectors.telegram.default_role == "guest"
    assert config.connectors.cli.default_role == "admin"


def test_invalid_default_role_is_rejected() -> None:
    # default_role is a typed Literal, so a typo (or a stale 'superuser') fails loudly
    # at load instead of silently opening or closing the gate.
    with pytest.raises(ValidationError):
        GaiaConfig.model_validate({"connectors": {"whatsapp": {"default_role": "superuser"}}})
