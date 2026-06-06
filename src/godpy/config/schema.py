"""Declarative schema for ``god.yaml`` — the non-secret, hot-swappable config.

Every field carries a default so a missing file or section degrades to today's
behaviour rather than erroring. Secrets (tokens, api keys) are *not* modelled here;
they stay in :class:`godpy.config.settings.Settings` (env). The lone exception is
``TelegramConnectorConfig.token``, which the store fills in *from env* after parsing
so the rest of the code has one place to read it — the YAML itself should leave it
blank.

``roles`` / ``tools`` / ``souls`` are typed but **not yet wired** into the runtime;
they are validated and carried forward so future work has a stable shape to build on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LLMConfig(BaseModel):
    """Which model/provider backs an agent."""

    provider: str = "gemini"
    model: str = "gemini-2.0-flash"


class GroupTrigger(BaseModel):
    """When God should respond inside a group chat."""

    mention_only: bool = True


class WhatsAppConnectorConfig(BaseModel):
    """WhatsApp connector toggle + access policy."""

    enabled: bool = False
    # Override the session db path; empty/None = Settings.whatsapp_session_db default.
    store_path: Path | None = None
    # Allowed sender ids. Empty = allow everyone (enforcement is a follow-up; see #10).
    allow: list[str] = Field(default_factory=list)
    group_trigger: GroupTrigger = Field(default_factory=GroupTrigger)
    default_soul: str = "god"
    default_role: str = "user"


class CLIConnectorConfig(BaseModel):
    """Local Textual TUI connector. Foreground-exclusive (cannot co-run)."""

    enabled: bool = False
    default_soul: str = "god"
    default_role: str = "admin"


class TelegramConnectorConfig(BaseModel):
    """Telegram connector toggle. ``token`` is injected from env by the store."""

    enabled: bool = False
    token: str | None = None


class ConnectorsConfig(BaseModel):
    """All connectors God can speak through."""

    whatsapp: WhatsAppConnectorConfig = Field(default_factory=WhatsAppConnectorConfig)
    cli: CLIConnectorConfig = Field(default_factory=CLIConnectorConfig)
    telegram: TelegramConnectorConfig = Field(default_factory=TelegramConnectorConfig)


class RoleConfig(BaseModel):
    """Per-role overrides. Typed but not yet wired (see issue #10 follow-ups)."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    # Allowed tool ids. Empty = all tools.
    tools: list[str] = Field(default_factory=list)


class ToolConfig(BaseModel):
    """Per-tool settings. Shape varies by tool, so extra keys are kept verbatim."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = True


class GodConfig(BaseModel):
    """Root of ``god.yaml``."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    admin: list[str] = Field(default_factory=list)
    connectors: ConnectorsConfig = Field(default_factory=ConnectorsConfig)
    # Forward-looking, validated-but-unwired sections.
    roles: dict[str, RoleConfig] = Field(default_factory=dict)
    tools: dict[str, ToolConfig] = Field(default_factory=dict)
    souls: dict[str, Any] = Field(default_factory=dict)
