"""Declarative schema for ``god.yaml`` — the non-secret, hot-swappable config.

Every field carries a default *and* a ``description`` so a missing file or section
degrades to today's behaviour, and the commented default file can be generated
straight from this schema (:mod:`godpy.config.scaffold`) — add a field here and the
scaffold updates itself, no second copy to maintain.

Secrets (tokens, api keys) are *not* modelled here; they stay in
:class:`godpy.config.settings.Settings` (env). ``tools`` is wired: it toggles which
registered tools are available (see :mod:`godpy.tools`). ``roles`` / ``souls`` are
typed but **not yet wired** into the runtime; they are validated and carried forward
so future work has a stable shape to build on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LLMConfig(BaseModel):
    """Which model/provider backs an agent."""

    provider: str = Field(default="gemini", description="LLM provider id.")
    model: str = Field(default="gemini-2.0-flash", description="Model id to call.")


class GroupTrigger(BaseModel):
    """When God should respond inside a group chat."""

    mention_only: bool = Field(
        default=True, description="Only respond in groups when God is mentioned."
    )


class WhatsAppConnectorConfig(BaseModel):
    """WhatsApp connector toggle + access policy."""

    enabled: bool = Field(default=False, description="Run the WhatsApp connector.")
    store_path: Path | None = Field(
        default=None, description="Session db path; empty = the default under the home dir."
    )
    allow: list[str] = Field(
        default_factory=list,
        description="Allowed sender ids; empty = everyone (enforcement is a follow-up).",
    )
    group_trigger: GroupTrigger = Field(default_factory=GroupTrigger)
    default_soul: str = Field(default="god", description="Soul used for new chats.")
    default_role: str = Field(default="user", description="Role assigned to senders.")


class CLIConnectorConfig(BaseModel):
    """Local Textual TUI connector. Foreground-exclusive (cannot co-run)."""

    enabled: bool = Field(
        default=False, description="Run the local terminal chat; foreground-exclusive."
    )
    default_soul: str = Field(default="god", description="Soul used in the CLI session.")
    default_role: str = Field(default="admin", description="Role for the local operator.")


class TelegramConnectorConfig(BaseModel):
    """Telegram connector toggle. Token comes from env, never this file."""

    enabled: bool = Field(default=False, description="Run the Telegram connector.")
    token: str | None = Field(
        default=None, description="Bot token; set via env GODPY_TELEGRAM_BOT_TOKEN, not here."
    )


class ConnectorsConfig(BaseModel):
    """All connectors God can speak through."""

    whatsapp: WhatsAppConnectorConfig = Field(default_factory=WhatsAppConnectorConfig)
    cli: CLIConnectorConfig = Field(default_factory=CLIConnectorConfig)
    telegram: TelegramConnectorConfig = Field(default_factory=TelegramConnectorConfig)


class RoleConfig(BaseModel):
    """Per-role overrides. Typed but not yet wired (see issue #10 follow-ups)."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    tools: list[str] = Field(
        default_factory=list, description="Allowed tool ids; empty = all tools."
    )


class ToolConfig(BaseModel):
    """Per-tool settings. Shape varies by tool, so extra keys are kept verbatim."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = Field(default=True, description="Whether the tool is available.")


class MemoryConfig(BaseModel):
    """Long-term (mem0) memory settings. Short-term is ADK's session state, no config."""

    enabled: bool = Field(
        default=True,
        description="Run long-term memory (mem0). Off = session-only, no cross-session recall.",
    )
    auto_ingest: bool = Field(
        default=True,
        description="Feed each turn to mem0 so it auto-extracts facts; off = remember-tool only.",
    )
    recall_limit: int = Field(
        default=5, description="How many memories load_memory returns per search."
    )
    vector_store: str | None = Field(
        default=None,
        description="mem0 vector store provider; empty = chroma (embedded, runs anywhere).",
    )


class LoggingConfig(BaseModel):
    """Log level + rotation. Applied once at startup (changes need a restart)."""

    level: str = Field(default="INFO", description="Root log level (DEBUG/INFO/WARNING/ERROR).")
    max_size_mb: int = Field(default=5, description="Rotate a log file once it exceeds this size.")
    backup_count: int = Field(default=5, description="How many rotated files to keep.")


class AgentBinding(BaseModel):
    """What is attached to a named agent: a voice + always-on folder skills."""

    communication_style: str | None = Field(
        default=None,
        description="Voice for this agent (human/caveman/ai); empty = default_communication_style.",
    )
    skills: list[str] = Field(
        default_factory=list,
        description="Skill ids (folder names under skills_dir) always loaded onto this agent.",
    )


class GodConfig(BaseModel):
    """Root of ``god.yaml``."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    admin: list[str] = Field(
        default_factory=list, description="Sender ids with admin privileges (reserved)."
    )
    connectors: ConnectorsConfig = Field(default_factory=ConnectorsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    default_communication_style: str = Field(
        default="human", description="Fallback voice for agents (human/caveman/ai)."
    )
    skills_dir: Path | None = Field(
        default=None, description="Skills folder; empty = the default under the home dir."
    )
    agents: dict[str, AgentBinding] = Field(
        default_factory=dict,
        description="Per-agent bindings; the root orchestrator uses key 'god'.",
    )
    # Forward-looking, validated-but-unwired sections.
    roles: dict[str, RoleConfig] = Field(
        default_factory=dict, description="Per-role overrides (not yet wired)."
    )
    tools: dict[str, ToolConfig] = Field(
        default_factory=dict,
        description="Per-tool settings keyed by tool id (e.g. web_search.engine: "
        "duckduckgo). Every tool is attached to agents by default; disable one with "
        "enabled: false.",
    )
    souls: dict[str, Any] = Field(
        default_factory=dict, description="Agent personas (not yet wired)."
    )
