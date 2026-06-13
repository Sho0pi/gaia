"""Declarative schema for ``gaia.yaml`` — the non-secret, hot-swappable config.

Every field carries a default *and* a ``description`` so a missing file or section
degrades to today's behaviour, and the commented default file can be generated
straight from this schema (:mod:`gaia.config.scaffold`) — add a field here and the
scaffold updates itself, no second copy to maintain.

Secrets (tokens, api keys) are *not* modelled here; they stay in
:class:`gaia.config.settings.Settings` (env). ``tools`` is wired: it toggles which
registered tools are available (see :mod:`gaia.tools`). ``roles`` / ``souls`` are
typed but **not yet wired** into the runtime; they are validated and carried forward
so future work has a stable shape to build on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class OpenAIConfig(BaseModel):
    """Provider-specific settings for OpenAI (applied when ``provider`` is ``openai``)."""

    use_oauth: bool = Field(
        default=False,
        description="Sign in with ChatGPT (run 'gaia llm auth openai') and use the "
        "subscription, instead of an OPENAI_API_KEY.",
    )


class LLMConfig(BaseModel):
    """Which model/provider backs an agent, plus per-provider settings."""

    provider: str = Field(
        default="gemini",
        description="LLM provider: gemini (GEMINI_API_KEY) or openai (needs the 'llm' dep group). "
        "Other litellm providers also work. Keys live in env.",
    )
    model: str = Field(
        default="gemini-2.0-flash", description="Model id, e.g. gemini-2.5-flash or gpt-4o."
    )
    # Per-provider blocks. Each provider gets its own settings sub-block here as needed
    # (openai today; anthropic/gemini/… can follow the same shape).
    openai: OpenAIConfig = Field(
        default_factory=OpenAIConfig, description="OpenAI-specific settings (e.g. use_oauth)."
    )


class MCPServerConfig(BaseModel):
    """One external MCP (Model Context Protocol) server to attach as tools.

    **Trust:** an MCP server is third-party code — a ``stdio`` server spawns a local
    process (e.g. via ``bunx``). Only configure servers you trust. **Secrets:** never put
    api keys in this file; list the env var names in ``env_passthrough`` and export them
    in the environment instead (they're copied into the server's process env).
    """

    name: str = Field(description="A short id for this server (used in logs / tool prefix).")
    enabled: bool = Field(default=True, description="Attach this server's tools.")
    transport: Literal["stdio", "sse", "http"] = Field(
        default="stdio", description="How to reach the server: stdio (local process), sse, or http."
    )
    # stdio transport
    command: str | None = Field(default=None, description="stdio: the executable (e.g. 'bunx').")
    args: list[str] = Field(default_factory=list, description="stdio: arguments to the command.")
    cwd: str | None = Field(
        default=None,
        description="stdio: working directory for the server process; empty = gaia's cwd.",
    )
    env: dict[str, str] = Field(
        default_factory=dict, description="stdio: literal (NON-secret) env vars for the server."
    )
    env_passthrough: list[str] = Field(
        default_factory=list,
        description="stdio: env var names to copy from gaia's environment into the server "
        "(keep secrets like API tokens here, not in 'env').",
    )
    # sse / http transports
    url: str | None = Field(default=None, description="sse/http: the server URL.")
    headers: dict[str, str] = Field(
        default_factory=dict, description="sse/http: request headers (e.g. an auth header)."
    )
    # selection
    tool_filter: list[str] = Field(
        default_factory=list,
        description="Only load these tool names from the server; empty = all of them. Use this "
        "to keep a chatty server from bloating the model's tool list.",
    )
    tool_prefix: str | None = Field(
        default=None, description="Prefix the server's tool names (avoid collisions / readability)."
    )


class MCPConfig(BaseModel):
    """External MCP servers whose tools are attached to Gaia and its souls."""

    servers: list[MCPServerConfig] = Field(
        default_factory=list,
        description="MCP servers to attach. Empty = no MCP (needs the 'mcp' dep group when set).",
    )


class BrowserConfig(BaseModel):
    """How Gaia drives a browser: Microsoft's playwright-mcp (default) or the native tools.

    The default ``mcp`` backend hands the browser to Microsoft's playwright-mcp server
    (launched via ``bunx``), exposing its full tool surface with no gaia code to keep.
    Its tradeoffs vs ``native`` (so the choice is informed):

    * **Runtime**: launched with ``bunx @playwright/mcp`` — needs bun on PATH. When the
      runtime is missing the backend falls back to ``native`` (with a warning) instead
      of crashing, like the fd/rg/playwright gates.
    * **URL safety**: ``native`` runs gaia's per-request SSRF guard (``validate_url``).
      ``mcp`` only enforces ``allowed_origins`` coarsely at the server; empty means **no
      restriction** (the browser can reach internal IPs). This is NOT equivalent to the
      native guard's per-redirect private-IP blocking.
    * **Isolation**: playwright-mcp drives ONE shared browser for the whole process; all
      souls share its tabs/cookies (``native`` gives each agent its own page).
      ``isolated`` keeps that profile in memory only.
    * **Observability**: ``mcp`` tool calls are logged only by ADK's generic
      after_tool_callback plugin, not gaia's per-tool ``done()``/``tool_used`` path.
    * **Hot-reload**: the backend and flags are read once at startup; editing them in
      gaia.yaml takes effect on the next restart.
    """

    model_config = ConfigDict(extra="allow")

    backend: Literal["native", "mcp"] = Field(
        default="mcp",
        description="Browser backend: 'mcp' (Microsoft playwright-mcp via bunx, default) "
        "or 'native' (gaia's built-in browser_* Playwright tools). 'mcp' falls back to "
        "'native' when the runtime isn't on PATH.",
    )
    runtime: str = Field(
        default="bunx",
        description="Executable that runs playwright-mcp (mcp backend). Default 'bunx' "
        "(bun). Must be on PATH or the backend falls back to native.",
    )
    package: str = Field(
        default="@playwright/mcp@latest",
        description="The playwright-mcp package spec passed to the runtime.",
    )
    headless: bool = Field(default=True, description="mcp backend: run the browser headless.")
    isolated: bool = Field(
        default=True,
        description="mcp backend: keep the browser profile in memory (no on-disk profile).",
    )
    browser: str = Field(
        default="chrome",
        description="mcp backend: which engine playwright-mcp drives "
        "(chrome/firefox/webkit/msedge).",
    )
    allowed_origins: list[str] = Field(
        default_factory=list,
        description="mcp backend: restrict navigation to these origins (semicolon-joined "
        "and passed to --allowed-origins). Empty = no restriction (note: COARSER than the "
        "native SSRF guard).",
    )
    tool_filter: list[str] = Field(
        default_factory=list,
        description="mcp backend: only load these playwright-mcp tool names; empty = all "
        "(~25-60). Trim to keep the model's tool list lean.",
    )


class CronDeliver(BaseModel):
    """Default delivery target for scheduled-job replies without a captured chat."""

    channel: str = Field(
        default="", description="Connector for cron replies (telegram/whatsapp); empty = log only."
    )
    chat: str = Field(
        default="",
        description="Chat id on that connector (telegram chat id / whatsapp user@server).",
    )


class CronConfig(BaseModel):
    """Scheduled jobs (the cron tool / `gaia cron`). Jobs live in ~/.gaia/cron.json."""

    enabled: bool = Field(
        default=True, description="Run the cron scheduler inside the daemon (gaia serve/start)."
    )
    deliver: CronDeliver = Field(
        default_factory=CronDeliver,
        description="Fallback delivery target for jobs created without a chat (e.g. via the CLI).",
    )


class VoiceConfig(BaseModel):
    """Local voice I/O: speech-to-text in (faster-whisper) + text-to-speech out (piper)."""

    enabled: bool = Field(
        default=True,
        description="Transcribe inbound voice messages and answer them like text "
        "(needs the 'voice' dep group; ignored when faster-whisper isn't installed).",
    )
    reply_with_voice: bool = Field(
        default=True,
        description="Answer a voice message with a voice message (piper TTS). Needs the "
        "'voice' group + the espeak-ng binary; falls back to text when unavailable.",
    )
    tts_voice: str = Field(
        default="en_US-ljspeech-high",
        description="piper voice model for spoken replies (downloaded on first use). Default "
        "is a high-quality female voice. See github.com/OHF-Voice/piper1-gpl for the voice "
        "list (e.g. en_US-amy-medium, en_US-hfc_female-medium).",
    )
    model: str = Field(
        default="base",
        description="faster-whisper model size: tiny/base/small/medium/large-v3. "
        "Bigger = better transcripts, slower + more RAM. Weights download on first use.",
    )
    language: str | None = Field(
        default=None,
        description="Force a transcription language (e.g. 'en', 'he'); empty = auto-detect.",
    )
    device: str = Field(
        default="cpu",
        description="Where to run the model: 'cpu' (anywhere) or 'cuda' (NVIDIA GPU).",
    )
    compute_type: str = Field(
        default="int8",
        description="Weight quantisation: 'int8' (smallest/fastest on CPU); GPUs usually "
        "pair device 'cuda' with 'float16'.",
    )


class GroupTrigger(BaseModel):
    """When Gaia should respond inside a group chat."""

    mention_only: bool = Field(
        default=True, description="Only respond in groups when Gaia is mentioned."
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
    default_soul: str = Field(default="gaia", description="Soul used for new chats.")
    default_role: Literal["admin", "user", "guest"] = Field(
        default="guest",
        description="Role for a first-seen sender (admin/user/guest). 'guest' is gated "
        "until an admin approves; seed admins via the top-level 'admin' list.",
    )


class CLIConnectorConfig(BaseModel):
    """Local Textual TUI connector. Foreground-exclusive (cannot co-run)."""

    enabled: bool = Field(
        default=False, description="Run the local terminal chat; foreground-exclusive."
    )
    default_soul: str = Field(default="gaia", description="Soul used in the CLI session.")
    default_role: Literal["admin", "user", "guest"] = Field(
        default="admin", description="Role for the local operator."
    )


class TelegramConnectorConfig(BaseModel):
    """Telegram connector toggle. Token comes from env, never this file."""

    enabled: bool = Field(default=False, description="Run the Telegram connector.")
    token: str | None = Field(
        default=None, description="Bot token; set via env GAIA_TELEGRAM_BOT_TOKEN, not here."
    )
    default_role: Literal["admin", "user", "guest"] = Field(
        default="guest",
        description="Role for a first-seen sender (admin/user/guest). 'guest' is gated "
        "until an admin approves; seed admins via the top-level 'admin' list.",
    )


# Connector names the daemon runs as background (asyncio) services. Target model
# (issue #107): the daemon is THE Gaia process — `gaia` (chat) attaches to it as a
# client over a local socket and errors out when the daemon isn't running, like every
# other channel. Until #107 lands, chat runs its own embedded Gaia as an interim step,
# which is why the cli connector isn't in this tuple.
BACKGROUND_CONNECTORS: tuple[str, ...] = ("whatsapp", "telegram")


class ConnectorsConfig(BaseModel):
    """All connectors Gaia can speak through."""

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


class MemoryProvider(BaseModel):
    """One mem0 component (llm / embedder / vector store): a provider + its config.

    Only ``provider`` is typed; any extra keys (``model``, ``host``, ``path``, …) are
    kept verbatim and passed straight to mem0 as that component's ``config``. **Secrets
    (api keys) belong in env, not here** — mem0 reads each provider's standard env var
    (``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``, …); the Gemini default reuses
    ``GEMINI_API_KEY`` automatically.
    """

    model_config = ConfigDict(extra="allow")

    provider: str = Field(
        default="gemini",
        description="mem0 provider id. Verified today: gemini (llm + embedder) and chroma "
        "(vector store). Others are passed through to mem0 but UNVERIFIED — LLM: "
        "openai/anthropic/minimax/litellm/ollama; embedder: openai/vertexai/fastembed/ollama "
        "(Anthropic has no embeddings); store: pgvector/qdrant/pinecone/…",
    )


class MemoryConfig(BaseModel):
    """Long-term (mem0) memory settings. Short-term is ADK's session state, no config."""

    enabled: bool = Field(
        default=True,
        description="Run long-term memory (mem0). Off = session-only, no cross-session recall.",
    )
    auto_ingest: bool = Field(
        default=True,
        description="Auto-extract facts from the conversation; off = remember-tool only. "
        "Turns are batched (see ingest_batch_size / ingest_interval_seconds) to keep cost down.",
    )
    ingest_batch_size: int = Field(
        default=10,
        description="Flush buffered turns to mem0 once this many have accumulated.",
    )
    ingest_interval_seconds: int = Field(
        default=3600,
        description="Also flush if this many seconds have passed since the first buffered turn.",
    )
    recall_limit: int = Field(
        default=5, description="How many memories load_memory returns per search."
    )
    # Provider-agnostic components. Defaults wire Gemini (reusing the agent's model; keys
    # come from env like the agent) + a local chroma store; override provider/model per
    # component. Only gemini + chroma are verified — see the provider field. Changing the
    # embedder invalidates the existing store (vectors live in its space).
    llm: MemoryProvider = Field(
        default_factory=lambda: MemoryProvider(provider="gemini"),
        description="Fact-extraction model. e.g. provider: openai, model: gpt-4o-mini.",
    )
    embedder: MemoryProvider = Field(
        default_factory=lambda: MemoryProvider(provider="gemini"),
        description="Vectoriser. e.g. provider: fastembed (local, no key) or openai.",
    )
    vector_store: MemoryProvider = Field(
        default_factory=lambda: MemoryProvider(provider="chroma"),
        description="Store. chroma (embedded, default) or e.g. provider: pgvector, host, port.",
    )


class CommandConfig(BaseModel):
    """Per-command settings. Extra keys are kept verbatim for future per-command options."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = Field(default=True, description="Whether the slash command is available.")


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


class GaiaConfig(BaseModel):
    """Root of ``gaia.yaml``."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    admin: list[str] = Field(
        default_factory=list,
        description="Channel-qualified sender ids seeded as admins, e.g. "
        "'whatsapp:972...@s.whatsapp.net' or 'telegram:12345'. Each is ensured to map to "
        "an admin user on startup; everyone else is learned at first contact.",
    )
    connectors: ConnectorsConfig = Field(default_factory=ConnectorsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    cron: CronConfig = Field(default_factory=CronConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    default_communication_style: str = Field(
        default="human", description="Fallback voice for agents (human/caveman/ai)."
    )
    skills_dir: Path | None = Field(
        default=None, description="Skills folder; empty = the default under the home dir."
    )
    agents: dict[str, AgentBinding] = Field(
        default_factory=dict,
        description="Per-agent bindings; the root orchestrator uses key 'gaia'.",
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
    commands: dict[str, CommandConfig] = Field(
        default_factory=dict,
        description="Per-command settings keyed by command name (e.g. forget.enabled: "
        "false). Every command is on by default.",
    )
