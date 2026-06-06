"""Runtime configuration. Secrets come from env / .env, never hardcoded."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central settings, populated from environment variables or a .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    # LLM backing the ADK agents (matches the .env: GEMINI_MODEL / GEMINI_API_KEY).
    model: str = Field(default="gemini-2.0-flash", validation_alias="GEMINI_MODEL")
    google_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")

    # Where reusable AgentCards are persisted.
    agent_registry_dir: Path = Field(
        default=Path("agent_registry"), validation_alias="GODPY_AGENT_REGISTRY_DIR"
    )

    # Connector credentials.
    telegram_bot_token: str | None = Field(
        default=None, validation_alias="GODPY_TELEGRAM_BOT_TOKEN"
    )
    whatsapp_phone_id: str | None = Field(default=None, validation_alias="GODPY_WHATSAPP_PHONE_ID")
    whatsapp_token: str | None = Field(default=None, validation_alias="GODPY_WHATSAPP_TOKEN")
    # Session db for the regular-account (neonize) backend. First run writes a QR
    # to the terminal; the paired session is persisted here so later runs skip it.
    whatsapp_session_db: Path = Field(
        default=Path.home() / ".godpy" / "whatsapp.db",
        validation_alias="GODPY_WHATSAPP_SESSION_DB",
    )

    # mem0 long-term memory.
    mem0_collection: str = Field(default="godpy", validation_alias="GODPY_MEM0_COLLECTION")

    @property
    def has_whatsapp_business(self) -> bool:
        """True when Cloud-API (pywa) creds are present; selects the business backend."""
        return bool(self.whatsapp_phone_id and self.whatsapp_token)


def get_settings() -> Settings:
    """Return a fresh Settings instance (env is re-read each call)."""
    return Settings()


def configure_adk_env(settings: Settings) -> None:
    """Bridge our key into the env var ADK / google-genai expects (``GOOGLE_API_KEY``)."""
    if settings.google_api_key:
        os.environ.setdefault("GOOGLE_API_KEY", settings.google_api_key)
