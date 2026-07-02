"""Declarative schema for ``gaia.yaml`` — the non-secret, hot-swappable config.

Every field carries a default *and* a ``description`` so a missing file or section
degrades to today's behaviour, and the commented default file can be generated
straight from this schema (:mod:`gaia.config.scaffold`) — add a field here and the
scaffold updates itself, no second copy to maintain.

Secrets (tokens, api keys) are *not* modelled here; they stay in
:class:`gaia.config.settings.Settings` (env). ``tools`` is wired: it toggles which
registered tools are available (see :mod:`gaia.tools`). ``souls.timeout_seconds`` is wired
(the delegate timeout); ``roles`` is wired — each role's ``capabilities`` drive the ACL
(see :mod:`gaia.acl`), gating which tools a user may call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OpenAIConfig(BaseModel):
    """Provider-specific settings for OpenAI (applied when ``provider`` is ``openai``)."""

    use_oauth: bool = Field(
        default=False,
        description="Sign in with ChatGPT (run 'gaia model') and use the "
        "subscription, instead of an OPENAI_API_KEY.",
    )


class LLMConfig(BaseModel):
    """Which model/provider backs an agent, plus per-provider settings."""

    provider: str = Field(
        default="gemini",
        description="LLM provider: gemini (GEMINI_API_KEY), openai (OPENAI_API_KEY), anthropic "
        "(ANTHROPIC_API_KEY), or openrouter (OPENROUTER_API_KEY) — all but gemini need the 'llm' "
        "dep group. Other litellm providers also work. Keys live in env.",
    )
    model: str = Field(
        default="gemini-2.0-flash",
        description="Model id, e.g. gemini-2.5-flash, gpt-4o, claude-sonnet-4-6, or (openrouter) "
        "anthropic/claude-sonnet-4-6.",
    )
    effort: str = Field(
        default="",
        description="Reasoning effort for thinking-capable models: minimal|low|medium|high "
        "(blank = provider default). Mapped per provider — OpenAI/Anthropic via litellm "
        "reasoning_effort, ChatGPT-OAuth via reasoning.effort, Gemini via thinking budget. "
        "Change it from chat with /effort.",
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
    owner: str = Field(
        default="",
        description="Canonical user id this server is private to; empty = shared with everyone. A "
        "user's agent gets only shared + their own servers, so a personal integration (with a "
        "personal token) stays private to whoever added it.",
    )
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
    """How Gaia drives a browser: gaia's own native tools (default) or Microsoft's playwright-mcp.

    The default ``native`` backend uses gaia's built-in ``browser_*`` Playwright tools, driving the
    Camoufox anti-detect engine (``engine``) — gaia owns the whole surface (stable tool schema, the
    per-request SSRF guard, per-agent isolation, anti-bot stealth). ``mcp`` is an **opt-in
    fallback** that hands the browser to Microsoft's playwright-mcp server (broader surface —
    tabs/PDF/network — but a third-party schema that drifts, and needs bun on PATH). The tradeoffs:

    * **Runtime**: ``native`` needs only the Python ``browser`` extra. ``mcp`` is launched with
      ``bunx @playwright/mcp`` — needs bun on PATH; when it's missing the backend falls back to
      ``native`` (with a warning) instead of crashing, like the fd/rg/playwright gates.
    * **URL safety**: ``native`` runs gaia's per-request SSRF guard (``validate_url``). ``mcp`` only
      enforces ``allowed_origins`` coarsely at the server; empty means **no restriction** (the
      browser can reach internal IPs) — NOT equivalent to native's per-redirect private-IP blocking.
    * **Isolation**: ``native`` gives each agent its own page. playwright-mcp drives ONE shared
      browser for the whole process; all souls share its tabs/cookies (``isolated`` keeps that
      profile in memory only).
    * **Observability**: ``native`` tool calls go through gaia's per-tool ``tool_used`` logging;
      ``mcp`` calls are only logged by ADK's generic after_tool_callback plugin.
    * **Hot-reload**: the backend and flags are read once at startup; editing them in gaia.yaml
      takes effect on the next restart.
    """

    model_config = ConfigDict(extra="allow")

    backend: Literal["native", "mcp"] = Field(
        default="native",
        description="Browser backend: 'native' (default — gaia's built-in browser_* tools driving "
        "the Camoufox engine) or 'mcp' (opt-in Microsoft playwright-mcp via bunx; needs bun on "
        "PATH and falls back to 'native' when the runtime is missing).",
    )
    runtime: str = Field(
        default="bunx",
        description="Executable that runs playwright-mcp (mcp backend only). Default 'bunx' "
        "(bun). Must be on PATH or the backend falls back to native.",
    )
    package: str = Field(
        default="@playwright/mcp@latest",
        description="The playwright-mcp package spec passed to the runtime.",
    )
    headless: bool | Literal["virtual"] = Field(
        default=True,
        description="Run the browser headless: true/false, or 'virtual' (camoufox engine, Linux + "
        "Xvfb) to run a real browser on a virtual display — stronger anti-detection than headless; "
        "falls back to headless if Xvfb/Linux is missing. `sudo apt install xvfb` to enable.",
    )
    isolated: bool = Field(
        default=True,
        description="mcp backend: keep the browser profile in memory (no on-disk profile).",
    )
    browser: str = Field(
        default="chromium",
        description="mcp backend: which engine playwright-mcp drives (chromium/chrome/firefox/"
        "webkit/msedge). 'chromium' is downloaded by `playwright install`; 'chrome' needs system "
        "Google Chrome (no ARM64 Linux build).",
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
    engine: Literal["chromium", "camoufox"] = Field(
        default="camoufox",
        description="native backend: which browser engine to drive. 'camoufox' (default) is an "
        "anti-detect Firefox that beats many bot walls (needs its Firefox build, fetched by "
        "`gaia update` / `python -m camoufox fetch`); 'chromium' is the plain Playwright build.",
    )
    humanize: bool = Field(
        default=True, description="camoufox engine: human-like cursor movement (anti-detection)."
    )
    locale: str = Field(
        default="", description="camoufox engine: locale, e.g. 'en-US' (empty = Camoufox default)."
    )
    os: Literal["", "windows", "macos", "linux"] = Field(
        default="", description="camoufox engine: OS to spoof in the fingerprint (empty = random)."
    )
    geoip: bool = Field(
        default=False, description="camoufox engine: match geolocation/timezone to the (proxy) IP."
    )
    block_images: bool = Field(
        default=False, description="camoufox engine: skip downloading images (faster, less data)."
    )
    viewport: str = Field(
        default="1280x1600",
        description="native backend: browser viewport as 'WxH'. Default 1280x1600 — a desktop "
        "width (Firefox can't emulate mobile, so a narrow viewport renders desktop CSS squished) "
        "at a 4:5 portrait so screenshots fit a chat preview without cropping. Empty = engine "
        "default.",
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


class AnalysisConfig(BaseModel):
    """The self-improve loop: gaia mines its own usage to grow skills/souls/memory."""

    enabled: bool = Field(
        default=False,
        description="Run the self-improve loop in the daemon — periodically analyze usage and "
        "apply new/refined skills, souls, and memories. Off by default (opt-in).",
    )
    interval_hours: float = Field(
        default=24.0, description="How often the improve cycle runs (hours)."
    )
    window_days: int = Field(default=7, description="How many days of usage each cycle analyzes.")
    autonomous: bool = Field(
        default=True,
        description="Apply proposals automatically. (A HITL review mode is a follow-up.)",
    )


class MonitorGithubConfig(BaseModel):
    """File GitHub issues for monitor findings (needs a GITHUB_TOKEN; off by default)."""

    create_issues: bool = Field(
        default=False,
        description="File a GitHub issue for file_issue findings. Needs GITHUB_TOKEN; falls back "
        "to notify-only if the token is missing.",
    )
    repo: str = Field(
        default="", description="Target repo 'owner/name' (e.g. 'Sho0pi/gaia'). Required to file."
    )
    label: str = Field(
        default="gaia-monitor", description="Label put on filed issues (also used to find dupes)."
    )


class MonitorConfig(BaseModel):
    """The self-monitoring loop: gaia mines its own error logs and reports problems."""

    enabled: bool = Field(
        default=False,
        description="Run the self-monitoring loop in the daemon — periodically read the error "
        "logs, judge what's a real problem, and report it. Off by default (opt-in).",
    )
    interval_hours: float = Field(
        default=24.0,
        description="How often the monitor cycle runs (hours). Also the per-signature report "
        "cooldown (the same error is reported at most once per cycle).",
    )
    window_hours: int = Field(
        default=24, description="How many hours of error logs each cycle analyzes."
    )
    notify: bool = Field(
        default=True, description="DM the admin about new findings (turn off for issues-only)."
    )
    github: MonitorGithubConfig = Field(default_factory=MonitorGithubConfig)


class MissionsConfig(BaseModel):
    """The missions task board engine (the dispatcher inside the daemon)."""

    enabled: bool = Field(
        default=True,
        description="Run the mission dispatcher inside the daemon — it executes board tasks "
        "(filed via task_create) on souls and pushes results.",
    )
    max_concurrent: int = Field(
        default=3,
        description="How many board tasks run at once — each runs on one soul, so this is "
        "the cap on simultaneously-busy souls/agents. Extra ready tasks wait their turn.",
    )
    poll_seconds: float = Field(
        default=2.0, description="How often the dispatcher polls the board for ready tasks."
    )
    max_depth: int = Field(
        default=3,
        description="Max subtask nesting depth on the board (a soul filing a subtask deeper "
        "than this is refused) — the runaway-nesting brake.",
    )
    max_tasks: int = Field(
        default=20,
        description="Max tasks a single mission may ever file (incl. done/failed). Hitting it "
        "pauses the mission and asks you before more work is created.",
    )
    max_hours: float = Field(
        default=0.0,
        description="Wall-clock budget per mission in hours; 0 = unbounded. Past it the mission "
        "pauses and asks you.",
    )
    consult_depth: int = Field(
        default=2,
        description="Max nesting depth for synchronous consult_soul calls (soul asks a soul a "
        "question) — bounds in-turn recursion.",
    )
    approval_classes: list[str] = Field(
        default_factory=list,
        description="Action classes that require human approval before a task runs "
        "(e.g. spend, book, send_as_me, destructive). A gated task parks in awaiting_approval "
        "and pushes you an 'approve?' — release with /task approve <id>.",
    )


class VoiceConfig(BaseModel):
    """Inbound voice notes: local speech-to-text via faster-whisper (the 'voice' group)."""

    enabled: bool = Field(
        default=True,
        description="Transcribe inbound voice messages and answer them like text "
        "(needs the 'voice' dep group; ignored when faster-whisper isn't installed).",
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
    """When Gaia should respond inside a group chat.

    Default is deliberately quiet: in a group Gaia answers only when it is *addressed*
    (@mentioned or someone replies to one of its messages). *Who* may trigger it is the
    user/role system's job (``users.json`` + the dispatcher's guest-drop) — known users
    pass, unknown senders are guests and are dropped — so there is no second allow-list here.
    """

    respond_in_groups: bool = Field(
        default=True,
        description="Master switch for group chats; false = ignore all group messages.",
    )
    mention_only: bool = Field(
        default=True,
        description="Only respond in groups when Gaia is @mentioned or replied to.",
    )


class WhatsAppConnectorConfig(BaseModel):
    """WhatsApp connector toggle + access policy.

    Access is governed by roles + guest-gating (a first-seen remote sender is a gated ``guest``
    until an admin approves). ``allow`` pre-approves specific senders past that gate from config;
    the rest of the identity graph (linked ids, per-user ACL) stays in ``users.json`` — see the
    access-control concept doc.
    """

    enabled: bool = Field(default=False, description="Run the WhatsApp connector.")
    group_trigger: GroupTrigger = Field(default_factory=GroupTrigger)
    show_active: bool = Field(
        default=True,
        description="Look active while working: blue-tick the message and show the 'typing…' "
        "(or 'recording audio…') indicator for the turn.",
    )
    default_role: Literal["admin", "user", "guest"] = Field(
        default="guest",
        description="Role for a first-seen sender (admin/user/guest). 'guest' is gated "
        "until an admin approves; seed admins via the top-level 'admin' list.",
    )
    allow: list[str] = Field(
        default_factory=list,
        description="Senders pre-allowed past the guest gate as 'user'. Any number format "
        "(digits, +, spaces, dashes) or a full '…@s.whatsapp.net' jid. Additive: adding "
        "pre-approves, removing does NOT demote (revoke with '/approve <id> guest').",
    )


class CLIConnectorConfig(BaseModel):
    """Local inline CLI chat connector. Foreground-exclusive (cannot co-run)."""

    enabled: bool = Field(
        default=False, description="Run the local terminal chat; foreground-exclusive."
    )
    default_role: Literal["admin", "user", "guest"] = Field(
        default="admin", description="Role for the local operator."
    )


class TelegramConnectorConfig(BaseModel):
    """Telegram connector toggle. The bot token is a secret: env ``GAIA_TELEGRAM_BOT_TOKEN`` only,
    never this file (gaia.yaml is hand-edited, not 0600) - so there's no token field here."""

    enabled: bool = Field(default=False, description="Run the Telegram connector.")
    default_role: Literal["admin", "user", "guest"] = Field(
        default="guest",
        description="Role for a first-seen sender (admin/user/guest). 'guest' is gated "
        "until an admin approves; seed admins via the top-level 'admin' list.",
    )
    allow: list[str] = Field(
        default_factory=list,
        description="Sender ids (numeric Telegram ids) pre-allowed past the guest gate as 'user'. "
        "Additive: adding pre-approves, removing won't demote (revoke via /approve <id> guest).",
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
    """Per-role ACL: the capabilities (groups / tool ids / ``*``) the role holds.

    Empty ``capabilities`` means "use the built-in default" (:data:`gaia.acl.groups.
    DEFAULT_ROLE_CAPS`); set it to override. A capability is a group name (``web``,
    ``shell``, ``manage_users``…), the wildcard ``*``, or a raw tool id.
    """

    capabilities: list[str] = Field(
        default_factory=list,
        description="ACL capabilities this role holds (group names like 'web'/'shell', "
        "'*' for all, or a raw tool id). Empty = built-in default for the role.",
    )


def _role_defaults() -> dict[str, RoleConfig]:
    """The built-in per-role capabilities, surfaced into gaia.yaml so every role's ACL is visible
    and editable there. Single source: :data:`gaia.acl.groups.DEFAULT_ROLE_CAPS` (lazy-imported)."""
    from gaia.acl.groups import DEFAULT_ROLE_CAPS

    return {role: RoleConfig(capabilities=list(caps)) for role, caps in DEFAULT_ROLE_CAPS.items()}


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
        description="Grow long-term memory automatically; off = remember-tool only. When a chat "
        "goes idle (sessions.idle_consolidate_minutes) gaia distils its important facts into mem0 "
        "— like a person processing a conversation after it ends.",
    )
    extraction_instructions: str = Field(
        default="",
        description="Override what long-term memory extracts (mem0 custom_instructions); "
        "empty = the built-in default (durable user facts only, no assistant action logs).",
    )
    recall_limit: int = Field(
        default=5, description="How many memories load_memory returns per search."
    )
    preload: bool = Field(
        default=True,
        description="At session start, distil the user's facts + recent projects into a "
        "profile baked into the prompt (always-on recall); off = the agent must call "
        "load_memory to recall anything.",
    )
    preload_limit: int = Field(
        default=20,
        description="Max bullet points the session-start profile keeps (importance-ranked).",
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


class SessionsConfig(BaseModel):
    """Durable conversation sessions: survive restarts, replay a window, consolidate on idle."""

    window_turns: int = Field(
        default=30,
        description="How many recent conversation turns gaia replays to the model each message. "
        "The whole conversation is kept on disk; older turns come back via long-term memory. "
        "Lower = fewer tokens per message, shorter verbatim memory.",
    )
    idle_consolidate_minutes: float = Field(
        default=30.0,
        description="After a conversation is idle this long, gaia distils its important facts into "
        "long-term memory and clears the session (a fresh, memory-informed start next time).",
    )


class SoulsConfig(BaseModel):
    """Settings for delegated souls. ``extra='allow'`` keeps room for the not-yet-wired
    agent-persona keys that used to live under this section."""

    model_config = ConfigDict(extra="allow")

    timeout_seconds: float = Field(
        default=300.0,
        description="Max seconds a delegated soul may run before the delegation is abandoned.",
    )
    session_idle_minutes: float = Field(
        default=30.0,
        description="Keep a soul's session warm between delegations so it resumes (instead of "
        "re-reading its workspace each time); evict it after this many minutes idle.",
    )


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
    missions: MissionsConfig = Field(default_factory=MissionsConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    monitor: MonitorConfig = Field(default_factory=MonitorConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    default_communication_style: str = Field(
        default="human", description="Fallback voice for agents (human/caveman/ai)."
    )
    skills_dir: Path | None = Field(
        default=None, description="Skills folder; empty = the default under the home dir."
    )
    skill_index: list[str] = Field(
        default_factory=list,
        description="Skill index urls (json manifests of {name, description, source}) that "
        "'skill search' / Gaia search for installable skills; empty = web-search fallback only.",
    )
    agents: dict[str, AgentBinding] = Field(
        default_factory=dict,
        description="Per-agent bindings; the root orchestrator uses key 'gaia'.",
    )
    # Forward-looking, validated-but-unwired sections.
    roles: dict[str, RoleConfig] = Field(
        default_factory=_role_defaults,
        description="Per-role ACL, keyed by role (admin/user/guest): the capabilities each holds. "
        "Pre-filled with the built-in defaults so you see and edit exactly what each role can do. "
        "A role you drop / leave with empty capabilities falls back to its built-in default.",
    )
    tools: dict[str, ToolConfig] = Field(
        default_factory=dict,
        description="Per-tool settings keyed by tool id (e.g. web_search.engine: "
        "duckduckgo). Every tool is attached to agents by default; disable one with "
        "enabled: false.",
    )
    souls: SoulsConfig = Field(
        default_factory=SoulsConfig, description="Delegated-soul settings (e.g. timeout_seconds)."
    )
    commands: dict[str, CommandConfig] = Field(
        default_factory=dict,
        description="Per-command settings keyed by command name (e.g. forget.enabled: "
        "false). Every command is on by default.",
    )
