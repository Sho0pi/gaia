"""Unit tests for the god.yaml schema parsing + defaults."""

from __future__ import annotations

from godpy.config import GodConfig

# Trimmed version of the prior-art POC from issue #10.
_POC = {
    "llm": {"provider": "gemini", "model": "gemini-3.1-flash-lite"},
    "admin": ["123456789"],
    "connectors": {
        "whatsapp": {
            "enabled": True,
            "allow": ["123456789", "987654321"],
            "group_trigger": {"mention_only": True},
        },
        "cli": {"enabled": True, "default_role": "admin"},
        "telegram": {"enabled": True},
    },
    "logging": {"level": "DEBUG", "max_size_mb": 10},
    "default_communication_style": "human",
    "skills_dir": "/custom/skills",
    "agents": {"god": {"communication_style": "caveman", "skills": ["caveman"]}},
    "roles": {"guest": {"tools": ["web_search"]}},
    "tools": {"web_extract": {"enabled": True, "max_chars": 8000}},
    "souls": {"god": None},
}


def test_parses_poc_sample() -> None:
    config = GodConfig.model_validate(_POC)

    assert config.llm.model == "gemini-3.1-flash-lite"
    assert config.admin == ["123456789"]
    assert config.connectors.whatsapp.enabled is True
    assert config.connectors.whatsapp.allow == ["123456789", "987654321"]
    assert config.connectors.whatsapp.group_trigger.mention_only is True
    assert config.connectors.cli.default_role == "admin"
    assert config.logging.level == "DEBUG"
    assert config.logging.max_size_mb == 10
    assert config.logging.backup_count == 5  # default kept
    assert config.default_communication_style == "human"
    assert str(config.skills_dir) == "/custom/skills"
    assert config.agents["god"].communication_style == "caveman"
    assert config.agents["god"].skills == ["caveman"]
    assert config.roles["guest"].tools == ["web_search"]
    # ToolConfig keeps unknown tool-specific keys verbatim (extra="allow").
    assert config.tools["web_extract"].model_extra == {"max_chars": 8000}


def test_empty_config_is_all_defaults() -> None:
    config = GodConfig.model_validate({})

    assert config.llm.model == "gemini-2.0-flash"
    assert config.admin == []
    # Every connector defaults to disabled — an empty file launches nothing.
    assert config.connectors.whatsapp.enabled is False
    assert config.connectors.cli.enabled is False
    assert config.connectors.telegram.enabled is False
    assert config.connectors.whatsapp.group_trigger.mention_only is True
    assert config.default_communication_style == "human"
