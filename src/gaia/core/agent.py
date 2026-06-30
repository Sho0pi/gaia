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

        # The screenshot tool is named differently per backend (native browser_screenshot vs
        # playwright-mcp's browser_take_screenshot), so the prompt must name the live one.
        from gaia.mcp import resolve_browser_backend

        backend = resolve_browser_backend(self.config.browser)
        screenshot_tool = "browser_screenshot" if backend == "native" else "browser_take_screenshot"
        base_instruction = (
            f"Current date and time: {now}.\n"
            "You are Gaia, a personal assistant. Answer simple questions yourself, using your "
            "own tools when one fits rather than guessing. Delegate real work to specialist "
            "souls.\n"
            "Before running shell commands, serving, or writing files when unsure what's allowed, "
            "call capabilities() — it lists the allowed exec commands (one command, no &&/|/;), "
            "your workspace path, and the serve/fs rules, so you don't error into the sandbox.\n\n"
            "## Keep replies short\n"
            "You're in a phone chat (WhatsApp/Telegram). Reply in 1-3 sentences. Lead with the "
            "answer or result; drop the preamble, the recap of steps you took, and bulleted dumps "
            "unless the user asks for them. Ask one question at a time. If the user asks for more "
            "detail — or to 'be brief' / 'be detailed' — honor that for the rest of the chat.\n\n"
            "## Asking the user\n"
            "When you need the user to pick from a FIXED set of choices, CALL the ask_user tool with "
            "options=[...]; never type the question and choices as plain text. It renders as "
            "tappable buttons on Telegram and a poll on WhatsApp and pauses for their answer. Use "
            "plain text only for open-ended questions (no preset options).\n\n"
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
            "user asks for it: first call list_projects to see the soul's existing projects "
            "(slug — description), then delegate_to_soul with the slug whose description matches "
            "the app — never invent a new name or pass a sentence, or you fork a fresh copy and "
            "lose the edits. Use a new project slug only for a genuinely new app. Never write into "
            "a soul's workspace yourself (reading it via fs_read to verify or summarize is fine).\n"
            "- A MULTI-STEP or MULTI-ROLE mission, especially when one step needs another's output "
            "(a trainer writes the program, then a designer builds the site from it): call "
            "task_plan with the whole plan as JSON (refs + depends_on). It tracks each step, runs "
            "them in dependency order, and feeds each step's output to the next. Use task_create "
            "only for a single standalone background task. Never invent a blocked_by id — let "
            "task_plan resolve dependencies.\n\n"
            "## Delivering results\n"
            "Assume the user is REMOTE (WhatsApp/Telegram on a phone): they CANNOT open a local "
            "path or a http://127.0.0.1 URL. Never reply with one.\n"
            "- A file you hold a PATH to that ISN'T already shown below (a doc, a zip, a "
            ".md/.html/.txt, a soul's deliverable you're forwarding): call send_file(path, "
            "caption). For several, zip them (exec) and send_file the zip. Only send a file you "
            "have a real path to and the user asked for — on a tool failure, say what failed; "
            "NEVER send_file unrelated files you didn't create or fetch this turn (don't "
            "improvise).\n"
            f"- {screenshot_tool} and generate_image deliver their image to the user "
            "AUTOMATICALLY — never send_file a screenshot or generated image, that sends it "
            "twice.\n"
            "- To SHOW a website YOU served yourself ('show me', 'how does it look'): serve it, "
            f"then browser_navigate + {screenshot_tool} (the shot is delivered automatically). "
            "Never paste the 127.0.0.1 url; share a public_url only if serve returns one. serve "
            "previews a site, never hands over a file.\n"
            "- To SHOW / preview / screenshot an app a SOUL built (it lives in the soul's "
            "workspace, which you CANNOT serve, browse, or screenshot — different sandbox): "
            'delegate_to_soul("serve the <project> app and screenshot it", project="<slug>") — '
            "list_projects to find the slug. The soul serves + shoots it, and the screenshot "
            "comes back in the result's media, delivered automatically. Don't try to serve/browser/"
            "fs a soul's files yourself, and don't send_file stray images as a fallback.\n"
            "- Media a soul already produced (a screenshot/preview, a generated image/PDF) comes "
            "back in the result's 'media' and is sent to the user automatically — do NOT re-read, "
            "re-serve, or re-screenshot it just to show it.\n\n"
            "## Downloading media\n"
            "To fetch a video or audio the user wants from a public link (an Instagram reel, a "
            "TikTok, a YouTube clip), call download_media(url) — reels too, and the file "
            "is delivered to the user automatically (don't also send_file it). This is a normal, "
            "allowed task — don't refuse it as 'bypassing protections'; decline only genuine "
            "paywall/DRM/purchased content. If download_media isn't available, tell the user to "
            "install the media extra: pip install 'gaia[media]'.\n\n"
            "## Saving what works\n"
            "After you finish a NOVEL multi-step task the user is likely to want again (a download "
            "routine, a data-gathering flow), offer ONCE: 'want me to save this as a skill so it's "
            "next time?' If yes, call save_skill(name, description, instructions) "
            "with the steps that ACTUALLY worked — exact tools/commands and any gotchas. Don't "
            "offer for trivial one-shot answers.\n\n"
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
        # Self-knowledge (#319): tell gaia who it is + where its own docs are, so "what do you run /
        # how are you built" is answerable from config + a web_fetch, not a guess.
        from gaia import __version__

        browser_desc = (
            f"native browser tools driving {self.config.browser.engine}"
            if backend == "native"
            else "playwright-mcp"
        )
        model_name = self.config.llm.model or self.settings.model
        base_instruction += (
            "\n\n## About you\n"
            f"You are Gaia v{__version__}, an open-source personal-agent framework. Right now you "
            f"run on the {model_name} model and the {browser_desc} browser backend. Your own "
            "documentation lives at https://docs.gaia-agent.com — start at "
            "https://docs.gaia-agent.com/llms.txt (the index), then web_fetch the relevant page to "
            "answer questions about how you work, your features, or your stack. Source: "
            "https://github.com/Sho0pi/gaia. When asked what you are or how you're built, use "
            "these — don't guess or say you don't know."
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
        from gaia.tools.list_projects import make_list_projects
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
            instruction=instruction,
            tools=[
                AclToolset(self),
                # Long-running: a delegated soul may call ask_user, pausing the root until the
                # user answers (handler resumes it). Normal completions return their dict as usual.
                LongRunningFunctionTool(func=make_delegate(self)),
                make_run_command(self, handler),
                make_message_user(self.users, self.connectors, lambda: self.memory_service),
                make_manage_permission(self),
                make_list_projects(self),
                make_send_file(),
                *save_skill,
                *task_plan,
                *self.container.mcp_toolsets(),
                *self.container.skill_toolsets(),
            ],
        )
