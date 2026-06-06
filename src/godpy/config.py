"""Runtime configuration. Secrets come from env / .env, never hardcoded."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central settings, populated from environment variables or a .env file."""

    model_config = SettingsConfigDict(env_prefix="GODPY_", env_file=".env", extra="ignore")

    # LLM backing the ADK agents.
    model: str = "gemini-2.0-flash"
    google_api_key: str | None = None

    # Where reusable AgentCards are persisted.
    agent_registry_dir: Path = Field(default=Path("agent_registry"))

    # Connector credentials.
    telegram_bot_token: str | None = None
    whatsapp_phone_id: str | None = None
    whatsapp_token: str | None = None

    # mem0 long-term memory.
    mem0_collection: str = "godpy"


def get_settings() -> Settings:
    """Return a fresh Settings instance (env is re-read each call)."""
    return Settings()
