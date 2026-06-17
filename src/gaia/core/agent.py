"""Gaia: the root orchestrator.

Gaia owns the factory, registry and memory. For a task it decides which subagent
should handle it, reusing a stored agent when one already fits. The concrete ADK
root-agent wiring is built lazily in :meth:`Gaia.build_root_agent`.

Build-once services (transcriber, memory, mcp/skill toolsets) live in
:class:`gaia.di.Container` as lazy singletons. Callers reach them as
``gaia.container.X()``; there are no pass-through wrappers on ``Gaia``. The
one exception is :attr:`memory_service`, which keeps a thin property because
its config-enabled gate has to run per-access (hot-reload aware). See
``CLAUDE.md`` → *Service lifecycle & DI*.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from dependency_injector import providers

from gaia.agents import AgentSpec
from gaia.communication import apply_communication_style
from gaia.config import ConfigSupplier, Settings, configure_adk_env, get_settings
from gaia.config.schema import AgentBinding
from gaia.di import Container
from gaia.models import resolve_model
from gaia.skills import attach_skills

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
        # each lives in gaia.di, not here. See CLAUDE.md → Service lifecycle & DI.
        self.skills_dir = self.container.skills_dir()
        self.souls = self.container.souls()
        self.users = self.container.users()
        self.tasks = self.container.tasks()  # the shared missions board (TaskStore)
        self.tools = self.container.tools()
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

    def ensure_agent(self, spec: AgentSpec) -> LlmAgent:
        """Get a subagent for ``spec`` — reused if known, created+stored if new."""
        return self.factory.create_or_reuse(spec)

    def known_souls(self) -> list[str]:
        """Keys of every subagent Gaia has already learned."""
        return self.souls.list_keys()

    def build_root_agent(self, handler: Any = None) -> LlmAgent:
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
        from google.adk.agents import BaseAgent, LlmAgent

        sub_agents: list[BaseAgent] = [
            self.factory.create_or_reuse(self.souls.get(key))  # type: ignore[arg-type]
            for key in self.known_souls()
        ]
        from datetime import datetime

        # Time-aware prompt (god PR #26): scheduled turns ("what day is it") and
        # cron-tool date math need the model to know now. Built per root-agent build;
        # the handler keeps the Runner (and thus this timestamp) for the session, so
        # it's approximate within a long-lived conversation — good enough for dates.
        now = datetime.now().strftime("%A, %Y-%m-%d %H:%M %Z").strip()
        base_instruction = (
            f"Current date and time: {now}.\n"
            "You are Gaia. Answer simple questions yourself, calling your own tools when one "
            "fits rather than guessing. For a single quick build that ONE specialist can do "
            "in one shot (e.g. 'write me a poem', a tiny script), call delegate_to_soul(task) "
            "— it finds or forges the right soul, runs it, and returns. When it returns, tell "
            "the user which soul handled it (say so explicitly when 'created' is true), then "
            "report the workspace path and the list of files the soul produced. You can open "
            "those deliverables directly (fs_read takes the absolute paths under the souls' "
            "workspaces), so read/verify/summarize them yourself when the user asks. But for "
            "a MULTI-ROLE or MULTI-STEP project (e.g. 'build a gym website' = a trainer writes "
            "the program AND a frontend designer builds the site from it), use task_plan, NOT "
            "repeated delegate_to_soul — the board tracks each step, lets the user list/iterate "
            "them, runs them with the right dependency order, and hands each step's output to "
            "the next. To "
            "schedule work for later or on a recurring basis (reminders, daily briefs), use "
            "the cron tool — it runs your message at the scheduled time and delivers the "
            "result to the user's chat. To send a message to a *different* person (not a "
            "reply to whoever you're talking to), call message_user(recipient, text) — "
            "recipient may be a known user's name/id or a raw phone; combine it with the "
            "cron tool for 'in 5 minutes text Grace ...'-style tasks.\n"
            "For multi-step or long-running work that should run in the background and "
            "survive restarts, use the task board: a daemon worker runs each task on a "
            "specialist soul and delivers the result. For a mission with MORE THAN ONE step "
            "— especially when one step needs another's output — call task_plan with the "
            "whole plan as JSON (tasks with local refs + depends_on); it wires the real "
            "dependency edges so a step waits for its inputs and receives their results + "
            "files. Use task_create only for a single standalone task. Never put a made-up "
            "id in blocked_by — let task_plan resolve dependencies.\n"
            "To manage SKILLS for the user (reusable know-how), use run_command with the "
            "'skill' command: run_command('skill', 'search <query>') to find installable "
            "skills, run_command('skill', 'install <name-or-git-url>') to add one, "
            "run_command('skill', 'list') to see what's installed. A freshly installed skill "
            "is usable right away. run_command only runs commands available to you."
        )
        bound = self.config.agents.get("gaia", AgentBinding())
        instruction = attach_skills(base_instruction, bound.skills, self.skills_dir)
        style = bound.communication_style or self.config.default_communication_style
        instruction = apply_communication_style(instruction, style)

        from gaia.core.acl_toolset import AclToolset
        from gaia.souls import make_delegate
        from gaia.tools.command import make_run_command
        from gaia.tools.message import make_message_user
        from gaia.tools.permission import make_manage_permission
        from gaia.tools.task import make_task_plan

        # delegate_to_soul and message_user are attached to the root only — souls (built
        # from self.tools) never receive them, so a soul can neither spawn souls nor text
        # arbitrary users. message_user needs the live connector registry on `self`.
        return LlmAgent(
            name="gaia",
            model=resolve_model(
                self.config.llm.model or self.settings.model,
                provider=self.config.llm.provider,
                use_oauth=self.config.llm.openai.use_oauth,
            ),
            description="Root orchestrator that routes tasks to specialized subagents.",
            instruction=instruction,
            tools=[
                AclToolset(self),
                make_delegate(self),
                make_run_command(self, handler),
                make_message_user(self.users, self.connectors),
                make_manage_permission(self),
                make_task_plan(self.tasks, max_tasks=self.config.missions.max_tasks),
                *self.container.mcp_toolsets(),
                *self.container.skill_toolsets(),
            ],
            sub_agents=sub_agents,
        )
