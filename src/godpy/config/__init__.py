"""godpy configuration.

Two complementary halves:

* :class:`Settings` — **secrets** from env / .env (tokens, api keys, paths).
* :class:`GodConfig` + :class:`ConfigSupplier` — the **non-secret, hot-swappable**
  ``god.yaml`` (which connectors are on, allow lists, model choice).

Re-exported here so ``from godpy.config import Settings`` keeps working after the
module became a package.
"""

from godpy.config.scaffold import render_default_yaml, write_default_config
from godpy.config.schema import (
    CLIConnectorConfig,
    CommandConfig,
    ConnectorsConfig,
    GodConfig,
    GroupTrigger,
    LLMConfig,
    MemoryConfig,
    MemoryProvider,
    OpenAIConfig,
    RoleConfig,
    TelegramConnectorConfig,
    ToolConfig,
    WhatsAppConnectorConfig,
)
from godpy.config.settings import Settings, configure_adk_env, get_settings
from godpy.config.store import ConfigSupplier

__all__ = [
    "CLIConnectorConfig",
    "CommandConfig",
    "ConfigSupplier",
    "ConnectorsConfig",
    "GodConfig",
    "GroupTrigger",
    "LLMConfig",
    "MemoryConfig",
    "MemoryProvider",
    "OpenAIConfig",
    "RoleConfig",
    "Settings",
    "TelegramConnectorConfig",
    "ToolConfig",
    "WhatsAppConnectorConfig",
    "configure_adk_env",
    "get_settings",
    "render_default_yaml",
    "write_default_config",
]
