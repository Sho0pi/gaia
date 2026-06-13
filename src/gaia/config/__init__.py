"""gaia configuration.

Two complementary halves:

* :class:`Settings` — **secrets** from env / .env (tokens, api keys, paths).
* :class:`GaiaConfig` + :class:`ConfigSupplier` — the **non-secret, hot-swappable**
  ``gaia.yaml`` (which connectors are on, allow lists, model choice).

Re-exported here so ``from gaia.config import Settings`` keeps working after the
module became a package.
"""

from gaia.config.scaffold import render_default_yaml, write_default_config
from gaia.config.schema import (
    BACKGROUND_CONNECTORS,
    BrowserConfig,
    CLIConnectorConfig,
    CommandConfig,
    ConnectorsConfig,
    CronConfig,
    GaiaConfig,
    GroupTrigger,
    LLMConfig,
    MCPConfig,
    MCPServerConfig,
    MemoryConfig,
    MemoryProvider,
    MissionsConfig,
    OpenAIConfig,
    RoleConfig,
    TelegramConnectorConfig,
    ToolConfig,
    VoiceConfig,
    WhatsAppConnectorConfig,
)
from gaia.config.settings import Settings, configure_adk_env, get_settings
from gaia.config.store import ConfigSupplier

__all__ = [
    "BACKGROUND_CONNECTORS",
    "BrowserConfig",
    "CLIConnectorConfig",
    "CommandConfig",
    "ConfigSupplier",
    "ConnectorsConfig",
    "CronConfig",
    "GaiaConfig",
    "GroupTrigger",
    "LLMConfig",
    "MCPConfig",
    "MCPServerConfig",
    "MemoryConfig",
    "MemoryProvider",
    "MissionsConfig",
    "OpenAIConfig",
    "RoleConfig",
    "Settings",
    "TelegramConnectorConfig",
    "ToolConfig",
    "VoiceConfig",
    "WhatsAppConnectorConfig",
    "configure_adk_env",
    "get_settings",
    "render_default_yaml",
    "write_default_config",
]
