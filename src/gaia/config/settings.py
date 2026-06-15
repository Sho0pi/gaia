"""Secret-bearing runtime settings. Secrets come from env / .env, never hardcoded.

This is the *secrets* half of gaia's configuration. Non-secret runtime toggles
(which connectors are on, allow lists, model choice) live in the hot-swappable
``gaia.yaml`` modelled by :mod:`gaia.config.schema` and served by
:class:`gaia.config.store.ConfigSupplier`. Keeping the two apart means a token never
has to be written to a file that is meant to be hand-edited and watched.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from gaia import constants


class Settings(BaseSettings):
    """Central settings, populated from environment variables or a .env file."""

    model_config = SettingsConfigDict(
        env_file=str(constants.ENV_FILE), extra="ignore", populate_by_name=True
    )

    # LLM backing the ADK agents (matches the .env: GEMINI_MODEL / GEMINI_API_KEY).
    model: str = Field(default="gemini-2.0-flash", validation_alias="GEMINI_MODEL")
    google_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    # OpenAI key for GPT models (provider: openai); read by litellm from the env.
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    # Anthropic key for Claude models (provider: anthropic); read by litellm from env.
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")

    # Where reusable AgentCards are persisted.
    agent_registry_dir: Path = Field(
        default=constants.AGENT_REGISTRY_DIR,
        validation_alias=f"{constants.ENV_PREFIX}AGENT_REGISTRY_DIR",
    )

    # The hot-swappable gaia.yaml (non-secret runtime config). Lives next to the
    # WhatsApp session db so all of the app's home state is under HOME_DIR.
    config_path: Path = Field(
        default=constants.CONFIG_PATH, validation_alias=f"{constants.ENV_PREFIX}CONFIG"
    )

    # Directory for rotating log files (system.log / events.jsonl / errors.log).
    log_dir: Path = Field(
        default=constants.LOG_DIR, validation_alias=f"{constants.ENV_PREFIX}LOG_DIR"
    )

    # Connector credentials.
    telegram_bot_token: str | None = Field(
        default=None, validation_alias=f"{constants.ENV_PREFIX}TELEGRAM_BOT_TOKEN"
    )
    whatsapp_phone_id: str | None = Field(
        default=None, validation_alias=f"{constants.ENV_PREFIX}WHATSAPP_PHONE_ID"
    )
    whatsapp_token: str | None = Field(
        default=None, validation_alias=f"{constants.ENV_PREFIX}WHATSAPP_TOKEN"
    )
    # Session db for the regular-account (neonize) backend. First run writes a QR
    # to the terminal; the paired session is persisted here so later runs skip it.
    whatsapp_session_db: Path = Field(
        default=constants.SESSION_DB, validation_alias=f"{constants.ENV_PREFIX}WHATSAPP_SESSION_DB"
    )

    # mem0 long-term memory.
    mem0_collection: str = Field(
        default=constants.APP_NAME, validation_alias=f"{constants.ENV_PREFIX}MEM0_COLLECTION"
    )

    @property
    def has_whatsapp_business(self) -> bool:
        """True when Cloud-API (pywa) creds are present; selects the business backend."""
        return bool(self.whatsapp_phone_id and self.whatsapp_token)


def get_settings(env_file: Path | None = None) -> Settings:
    """Return a fresh Settings instance (env is re-read each call).

    ``env_file`` overrides the default home ``.env`` (``--env-file`` on the command).
    """
    if env_file is not None:
        return Settings(_env_file=str(env_file))  # type: ignore[call-arg]
    return Settings()


#: Telemetry/analytics kill-switches for our dependency stack, set (via ``setdefault``,
#: so a user can still opt back in) before any backend imports. ``DO_NOT_TRACK`` is the
#: cross-vendor consoledonottrack.com standard several libs honour; the rest are the
#: specific flags each lib reads at import. ``OTEL_SDK_DISABLED`` also quiets ADK's OTel
#: metrics emitter (the "missing token usage metadata" warning for non-Gemini models).
_TELEMETRY_OFF = {
    "DO_NOT_TRACK": "1",
    "OTEL_SDK_DISABLED": "true",  # OpenTelemetry SDK (ADK traces + metrics) → no-op
    "ANONYMIZED_TELEMETRY": "False",  # chromadb / posthog
    "CHROMA_TELEMETRY_IMPL": "none",  # chromadb belt-and-suspenders
    "MEM0_TELEMETRY": "false",  # mem0 posthog
    "LITELLM_TELEMETRY": "False",  # litellm (non-Gemini models)
    "HF_HUB_DISABLE_TELEMETRY": "1",  # huggingface (faster-whisper weights)
}


def configure_adk_env(settings: Settings) -> None:
    """Bridge our keys into the env vars the model backends expect, and silence telemetry.

    ADK / google-genai read ``GOOGLE_API_KEY``; litellm (GPT models) reads
    ``OPENAI_API_KEY``. Each is exported only when present. Telemetry kill-switches
    (:data:`_TELEMETRY_OFF`) are set first so no dependency phones home; all via
    ``setdefault`` so an operator who explicitly sets one wins.
    """
    for name, value in _TELEMETRY_OFF.items():
        os.environ.setdefault(name, value)
    if settings.google_api_key:
        os.environ.setdefault("GOOGLE_API_KEY", settings.google_api_key)
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    if settings.anthropic_api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)
