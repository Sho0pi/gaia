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
from gaia.models import resolve_model, thinking_planner
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
        base_instruction = (
            f"Current date and time: {now}.\n"
            "You are Gaia, a personal assistant. Answer simple questions yourself, using your "
            "own tools when one fits rather than guessing. Delegate real work to specialist "
            "souls.\n"
            "Before running shell commands, serving, or writing files when unsure what's allowed, "
            "call capabilities() — it lists the allowed exec commands (one command, no &&/|/;), "
            "your workspace path, and the serve/fs rules, so you don't error into the sandbox.\n\n"
            "## Delegating work\n"
            "- A single quick build ONE specialist can do in one shot (a poem, a tiny script, a "
            "page): call delegate_to_soul(task). It finds or forges the soul, runs it, and returns "
            "the workspace + files. Tell the user which soul handled it (say so when 'created' is "
            "true).\n"
            "- delegate_to_soul runs the soul to completion in ONE call. When it returns "
            "status=success the work is DONE — deliver the result to the user and STOP. Do NOT "
            "call delegate_to_soul (or task_create) again for that same task. If it returns "
            "status=error, tell the user what failed (the error message) — do not silently retry "
            "or improvise a different tool.\n"
            "- To CHANGE or extend a soul's project (dark mode, add a page) — and ONLY when the "
            "user asks for it: call delegate_to_soul again with the SAME project slug. Never "
            "write into a soul's workspace yourself (reading it via fs_read to verify or summarize "
            "is fine).\n"
            "- A MULTI-STEP or MULTI-ROLE mission, especially when one step needs another's output "
            "(a trainer writes the program, then a designer builds the site from it): call "
            "task_plan with the whole plan as JSON (refs + depends_on). It tracks each step, runs "
            "them in dependency order, and feeds each step's output to the next. Use task_create "
            "only for a single standalone background task. Never invent a blocked_by id — let "
            "task_plan resolve dependencies.\n\n"
            "## Delivering results\n"
            "Assume the user is REMOTE (WhatsApp/Telegram on a phone): they CANNOT open a local "
            "path or a http://127.0.0.1 URL. Never reply with one.\n"
            "- A file (doc, image, audio, a .md/.html/.txt, any soul deliverable): call "
            "send_file(path, caption). For several, zip them (exec) and send_file the zip.\n"
            "- To SHOW a website ('show me', 'how does it look'): serve it, then browser_navigate "
            "+ browser_take_screenshot so the screenshot goes back. Never paste the 127.0.0.1 url; "
            "share a public_url only if serve returns one. serve previews a site, never hands over "
            "a file.\n"
            "- Media a soul already produced (a screenshot/preview, a generated image/PDF) comes "
            "back in the result's 'media' and is sent to the user automatically — do NOT re-read, "
            "re-serve, or re-screenshot it just to show it.\n\n"
            "## Scheduling & messaging\n"
            "- cron: run your message later or on a schedule (reminders, daily briefs); it "
            "delivers the result to the user's chat.\n"
            "- message_user(recipient, text): message a DIFFERENT person (not a reply to whoever "
            "you're talking to); recipient is a known name/id or a raw phone. Combine with cron "
            "for 'in 5 minutes text Grace ...'.\n\n"
            "## Commands & admin\n"
            "run_command runs your slash-commands — pass the whole line, e.g. run_command('skill "
            "install <git-url>'), run_command('skill list'). Use it to manage SKILLS (a freshly "
            "installed skill is usable right away) and, for an ADMIN user, users/permissions: "
            "run_command('grant <user> <capability>'), run_command('approve <user> <role>'), "
            "run_command('users'). If it returns an error, tell the user what it said."
        )
        # Memory guidance only when long-term memory is on — so the prompt never advertises
        # the remember/load_memory tools or a <USER_PROFILE> block that aren't attached.
        # (Gated on memory.enabled, NOT on `profile`: an empty store still has the tools.)
        if self.config.memory.enabled:
            base_instruction += (
                "\n\n## Memory\n"
                "You have long-term memory of this user — facts + recent projects under "
                "<USER_PROFILE> above. Use it; don't re-ask. Save durable things (preferences, "
                "identity, ongoing context) with the remember tool. For older details not in the "
                "profile, load_memory(query) searches it."
            )
        bound = self.config.agents.get("gaia", AgentBinding())
        instruction = attach_skills(base_instruction, bound.skills, self.skills_dir)
        style = bound.communication_style or self.config.default_communication_style
        instruction = apply_communication_style(instruction, style)

        # Profile recall: a compact, importance-ranked block of what gaia knows about the
        # user (durable facts + recent projects), distilled by one LLM call when the handler
        # builds this agent (session start / config reload) and baked into the prompt — like
        # the timestamp above. Always fresh per session; deep lookups stay in load_memory.
        if profile:
            instruction += (
                "\n\nWhat you know about the user (long-term memory + recent projects):\n"
                f"<USER_PROFILE>\n{profile}\n</USER_PROFILE>"
            )

        from google.adk.tools.long_running_tool import LongRunningFunctionTool

        from gaia.core.acl_toolset import AclToolset
        from gaia.souls import make_delegate
        from gaia.tools.command import make_run_command
        from gaia.tools.message import make_message_user
        from gaia.tools.permission import make_manage_permission
        from gaia.tools.send_file import make_send_file
        from gaia.tools.task import make_task_plan

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
            instruction=instruction,
            tools=[
                AclToolset(self),
                # Long-running: a delegated soul may call ask_user, pausing the root until the
                # user answers (handler resumes it). Normal completions return their dict as usual.
                LongRunningFunctionTool(func=make_delegate(self)),
                make_run_command(self, handler),
                make_message_user(self.users, self.connectors, lambda: self.memory_service),
                make_manage_permission(self),
                make_send_file(),
                make_task_plan(self.tasks, max_tasks=self.config.missions.max_tasks),
                *self.container.mcp_toolsets(),
                *self.container.skill_toolsets(),
            ],
        )
