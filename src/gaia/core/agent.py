"""Gaia: the root orchestrator.

Gaia owns the factory, registry and memory. For a task it decides which subagent
should handle it, reusing a stored agent when one already fits. The concrete ADK
root-agent wiring is built lazily in :meth:`Gaia.build_root_agent`.

Build-once services (transcriber, memory, mcp/skill toolsets) live in
:class:`gaia.di.Container` as lazy singletons. Callers reach them as
``gaia.container.X()``; there are no pass-through wrappers on ``Gaia``. The
one exception is :attr:`memory_service`, which keeps a thin property because
its config-enabled gate has to run per-access (hot-reload aware). See
``AGENTS.md`` → *Service lifecycle & DI*.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from dependency_injector import providers

from gaia.agents import AgentSpec
from gaia.config import ConfigSupplier, Settings, configure_adk_env, get_settings
from gaia.config.schema import AgentBinding
from gaia.di import Container
from gaia.models import resolve_model, thinking_planner

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.agents import LlmAgent

    from gaia.config import GaiaConfig
    from gaia.memory import Mem0MemoryService


class Gaia:
    """Top-level agent that spawns, stores and reuses task-specific subagents."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        configure_adk_env(self.settings)
        self.config_supplier = ConfigSupplier(self.settings.config_path)
        self.container = Container(
            settings=providers.Object(self.settings),
            config_supplier=providers.Object(self.config_supplier),
        )
        # The container is the single composition root; Gaia pulls the handles it exposes
        # to the rest of the code under their established names (facade). Construction of
        # each lives in gaia.di, not here. See AGENTS.md → Service lifecycle & DI.
        self.skills_dir = self.container.skills_dir()
        self.souls = self.container.souls()
        self.users = self.container.users()
        self.projects = self.container.projects()  # each (user, soul)'s current project slug
        self.tasks = self.container.tasks()  # the shared missions board (TaskStore)
        self.tools = self.container.tools()
        self.soul_sessions = self.container.soul_sessions()  # warm per-(soul, project) sessions
        self.factory = self.container.factory()
        # Live proactive senders (connector name → object with ``send_to``); the launcher
        # populates this same dict once connectors are running (empty outside the daemon).
        self.connectors: dict[str, Any] = self.container.connectors()
        self._closed = False

    async def close(self) -> None:
        """Release every async resource on the *running* loop (idempotent, best-effort).

        Two cleanup queues run, both no-ops when their owner was never touched:
        ``tools.aclose()`` releases the per-tool managers (shell processes,
        browser sessions); ``container.lifecycle().aclose()`` releases the
        services the container built (mcp stdio children, skill toolsets).
        Called while the host loop is still alive so we don't fall through to
        the tool managers' ``atexit`` hooks (which would raise
        'Event loop is closed').
        """
        if self._closed:
            return
        self._closed = True
        await self.tools.aclose()
        await self.container.lifecycle().aclose()

    async def __aenter__(self) -> Gaia:
        """``async with Gaia(...):`` — :meth:`close` runs on exit, exceptions included."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    @property
    def config(self) -> GaiaConfig:
        """The live, hot-reloaded ``gaia.yaml`` config (re-read on file change)."""
        return self.config_supplier.current

    @property
    def memory_service(self) -> Mem0MemoryService | None:
        """The long-term memory service (mem0), gated by the live ``memory.enabled`` flag.

        Kept as a property (rather than letting callers use
        ``gaia.container.memory_service()`` directly) because the
        ``memory.enabled`` check has to run **per-access** — ``gaia.yaml``
        hot-reload can flip the flag at runtime, and seven callers across the
        codebase (handler, delegate, slash commands, the remember tool) rely
        on getting ``None`` back when memory is off. The container singleton
        underneath is still built at most once.
        """
        if not self.config.memory.enabled:
            return None
        service: Mem0MemoryService = self.container.memory_service()
        return service

    @property
    def session_service(self) -> Any:
        """The shared durable ADK session store (built once; survives restarts). Lazy: the heavy
        ADK import only happens on first access, keeping ``Gaia()`` construction cheap."""
        return self.container.session_service()

    def ensure_agent(self, spec: AgentSpec) -> LlmAgent:
        """Get a subagent for ``spec`` — reused if known, created+stored if new."""
        return self.factory.create_or_reuse(spec, effort=self.config.llm.effort)

    def known_souls(self) -> list[str]:
        """Keys of every subagent Gaia has already learned."""
        return self.souls.list_keys()

    def build_root_agent(self, handler: Any = None, *, profile: str | None = None) -> LlmAgent:
        """Construct the ADK root agent with all known subagents attached.

        ``handler`` (the live :class:`~gaia.core.handler.GaiaHandler`, passed by it) is
        threaded into the root-only ``run_command`` tool so Gaia can run handler-dependent
        commands (``/reset``) for the user; ``None`` for handler-free callers (dev web).

        Registry tools are attached through :class:`~gaia.core.acl_toolset.AclToolset`, a
        dynamic toolset ADK re-resolves every turn against the caller's *current*
        capabilities — so ``/grant`` / ``/revoke`` take effect on the next message without
        rebuilding the agent (the conversation is preserved). The hard security gate is
        :class:`gaia.core.plugins.ToolPermissionPlugin`; the toolset is the UX layer.

        Deferred ADK import keeps the rest of Gaia importable without a model.
        """
        from datetime import datetime

        from google.adk.agents import LlmAgent

        # Time-aware prompt (god PR #26): scheduled turns ("what day is it") and
        # cron-tool date math need the model to know now. Built per root-agent build;
        # the handler keeps the Runner (and thus this timestamp) for the session, so
        # it's approximate within a long-lived conversation — good enough for dates.
        now = datetime.now().strftime("%A, %Y-%m-%d %H:%M %Z").strip()

        from gaia.core.prompt import build_dynamic_instruction, build_static_instruction

        bound = self.config.agents.get("gaia", AgentBinding())
        style = bound.communication_style or self.config.default_communication_style
        # Static block (ADK static_instruction): identity, tool rules, skills, voice, and the
        # operator's GAIA.md — identical for every user/session of this instance, so the provider
        # caches it once and reuses it every turn. The dynamic tail is the only per-request content:
        # the current time + this user's <USER_PROFILE>, sent as user content after the cache.
        static_instruction = build_static_instruction(
            self.config, self.settings, self.skills_dir, style=style
        )
        dynamic_instruction = build_dynamic_instruction(now, profile)

        from google.adk.tools.long_running_tool import LongRunningFunctionTool

        from gaia.core.acl_toolset import AclToolset
        from gaia.souls import make_delegate
        from gaia.tools.command import make_run_command
        from gaia.tools.list_projects import make_list_projects
        from gaia.tools.manage_mcp import make_manage_mcp
        from gaia.tools.message import make_message_user
        from gaia.tools.permission import make_manage_permission
        from gaia.tools.registry import _is_enabled
        from gaia.tools.save_skill import NAME as SAVE_SKILL
        from gaia.tools.save_skill import make_save_skill
        from gaia.tools.send_file import make_send_file
        from gaia.tools.task import TASK_PLAN, make_task_plan

        # Root-only tools that are still on-by-default-but-config-gateable like any registry tool
        # (tools.<id>.enabled=false turns them off): save_skill ("learn & grow") and task_plan.
        save_skill = (
            [make_save_skill(self.skills_dir)] if _is_enabled(self.config, SAVE_SKILL) else []
        )
        task_plan = (
            [make_task_plan(self.tasks, max_tasks=self.config.missions.max_tasks)]
            if _is_enabled(self.config, TASK_PLAN)
            else []
        )

        # delegate_to_soul and message_user are attached to the root only — souls (built
        # from self.tools) never receive them, so a soul can neither spawn souls nor text
        # arbitrary users. message_user needs the live connector registry on `self`.
        root_model = self.config.llm.model or self.settings.model
        return LlmAgent(
            name="gaia",
            model=resolve_model(
                root_model,
                provider=self.config.llm.provider,
                use_oauth=self.config.llm.openai.use_oauth,
                effort=self.config.llm.effort,
            ),
            planner=thinking_planner(self.config.llm.provider, root_model, self.config.llm.effort),
            description="Root orchestrator that routes tasks to specialized subagents.",
            static_instruction=static_instruction,
            instruction=dynamic_instruction,
            tools=[
                AclToolset(self),
                # Long-running: a delegated soul may call ask_user, pausing the root until the
                # user answers (handler resumes it). Normal completions return their dict as usual.
                LongRunningFunctionTool(func=make_delegate(self)),
                make_run_command(self, handler),
                make_message_user(self.users, self.connectors, lambda: self.memory_service),
                make_manage_permission(self),
                make_manage_mcp(self),
                make_list_projects(self),
                make_send_file(),
                *save_skill,
                *task_plan,
                # MCP toolsets scoped to the current user: shared + their own private servers
                # (handler-free callers like the dev web UI get shared only).
                *self.container.mcp_toolsets_manager().for_user(handler.user_id if handler else ""),
                *self.container.skill_toolsets(),
            ],
        )
