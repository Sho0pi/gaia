# Gaia

AI agent inspired by openclaw, hermes-agent, picoclaw. **Gaia** agent answer
simple things itself, delegate complex tasks to **souls** — specialist
subagents it forge once, **store, reuse** (never recreate). Two-tier
memory: short-term (ADK session) + long-term (mem0, grow day by day).

> **Renamed `godpy`/`God` → `gaia`/`Gaia` (PR #111).** Old issues, PRs, commit
> messages, branch names still say "godpy"/"God" — mean this project /
> root agent. Package `gaia`, CLI `gaia`, config `~/.gaia/gaia.yaml`,
> env vars `GAIA_*`, root orchestrator in `src/gaia/core/` (class `Gaia`).

## Core rule: reuse, don't rebuild
Before writing custom, check for proven library (many stars / used by
similar agent projects / official). Prefer integrate over reinvent. When
unsure which library, delegate to `lib-researcher` subagent.

## Stack
- Python 3.11+, managed by **uv** (never pip / poetry / venv by hand).
- `google-adk` — agent runtime + LLM skeleton (LiteLLM wrapper for non-Gemini).
- `a2a-sdk` — agent↔subagent comms (ADK has native A2A support).
- `mem0ai` — long-term memory. ADK session `state` — short-term memory.
- `python-telegram-bot`, `pywa`, `neonize` — connectors; `textual` — chat TUI.

## Architecture (src/gaia/)
- `core/` — `agent.py` Gaia (root orchestrator), `handler.py` text↔ADK-Runner glue
  (one handler == one conversation), `dispatch.py` resolves inbound sender to
  `users.User` + role, gates guests, routes to cached per-user handler, `plugins.py`
  tool-call logging.
- `users/` — cross-channel identities: `(channel, sender)` → canonical `user_id` + role
  (admin/user/guest), persisted to `~/.gaia/users.json`. Memory/sessions key off
  `user.id`, so one person shares memory across channels. Admins seeded from
  `config.admin`; others learned at first contact + managed by chat commands.
- `souls/` — spawn/reuse loop: `smith.py` decides reuse-vs-forge (structured
  output); `delegate.py` root-only `delegate_to_soul` tool (nested Runner,
  sandboxed workspace, before/after file diff).
- `agents/` — `spec.py` AgentSpec, `factory.py` spec→`LlmAgent`, `registry.py`
  souls persisted as JSON → reuse, don't recreate.
- `tools/` — LLM-callable tools; `registry.py` name→callable, **on by default**,
  gated by `tools.<id>.enabled`. `fs/` sandboxed per agent. → `new-tool` skill.
  Browser dual-backend: `browser.backend` (default `mcp`) drives Microsoft's
  playwright-mcp via `bunx` (full tool surface, attached in `Gaia.mcp_toolsets`); set
  `native` for built-in `browser_*` tools. Missing bun falls back to native. See
  `BrowserConfig` in `config/schema.py` for SSRF/isolation/observability tradeoffs.
- `commands/` — in-chat slash commands (`/help`, `/reset`, …): one class per file,
  handled out-of-band, never reach model. → `new-command` skill.
- `memory/` — `backend.py` builds mem0, `service.py` adapts it to ADK's
  `BaseMemoryService` (auto-ingest batching + `remember`/`load_memory` tools);
  `profile.py` distils the session-start `<USER_PROFILE>`. Full design:
  `docs/memory-design.md`.
- `missions/` — task board + engine. `store.py` (stdlib sqlite3/WAL, `~/.gaia/tasks.db`):
  `Task` rows + `TaskStore` CRUD/`ready_tasks`. Per-user `owner` (≠ `created_by` agent).
  `dispatcher.py` `MissionDispatcher` (P2) runs in daemon: polls ready tasks → runs each
  on soul via `souls/run.py:execute_decision` → posts result+artifacts → dependents consume
  upstream results; `notify.py` pushes result to task's notify target (chat→owner→
  cron default). Surfaced by `task_*`, `/tasks`, `gaia tasks`. Missions epic (#134, design
  `docs/missions-design.md`); status enum maps to A2A `TaskState` (P5 bridge).
- `connectors/` — thin I/O adapters only (cli TUI, telegram, whatsapp, whatsapp_web);
  each extracts sender id + display name, calls channel-bound `Dispatch`
  callable `(sender_id, name, text, send)` from `base.py` (dispatcher resolves
  user); `Send`/`Reply`/`Handler` also live there. → `new-connector` skill.
- `config/` — `settings.py` secrets (env only), `schema.py` gaia.yaml (hot-reloaded
  by `store.py`); commented default file **generated from schema**
  (`scaffold.py`) — never hand-maintain second copy.
- `providers/openai/` — Sign in with ChatGPT (OAuth device flow + Responses backend).
- Entry: `gaia` CLI (`cli/`, Typer; `[project.scripts]`) → `app.py`
  (`run_cli` / `run` / `run_dev` / `run_auth`).
- `src/gaia/agents|souls` = gaia's RUNTIME agents. `.claude/agents/` = Claude Code
  DEV agents that help build gaia. Don't confuse them.

## Commands (always via uv, from repo root)
- Install: `uv sync --all-groups`
- Lint + fix: `uv run ruff check --fix . && uv run ruff format .`
- Types: `uv run mypy src`
- Test: `uv run pytest`
- Run: `uv run gaia` (TUI) / `gaia dev` (ADK web UI) / `gaia llm auth openai`
Do NOT `cd` into subdirs to run tools.

## Tests — what goes in which tier
- `tests/unit/` — offline, fake/monkeypatch-driven, no keys, no network, fast.
  Every change ships these.
- `tests/system/` — touches something real: model key (skipif-gated on
  `GEMINI_API_KEY`), native lib (`importorskip`), or live external resource.
  CI stays green without secrets because of guards — never remove them.
- Feature not done until both tiers exist and green.

## How to work here (senior R&D standard)
1. **Plan first.** For new feature, study how ADK samples (`google/adk-samples`)
   or similar agent repos solved it before coding. Use `/plan-feature`.
2. **Match library idiom.** Read lib's own source/examples; write in its style
   (ADK agent patterns, A2A card shapes). Don't invent conventions. Prefer ADK's
   **public** API — never reach into `_private` attributes of ADK objects.
3. **Test always** (see tiers above). Green tests or not done.
4. **Self-review.** After task, critique own diff: what's weak, what you'd
   change next. Put it in PR.
5. **New feature mid-task →** open GitHub issue (`gh issue create`) with right
   label + proposed approach. Don't silently scope-creep.
6. **Finished feature →** open PR (`gh pr create`) with summary, test evidence,
   self-feedback. Never commit to master — branch first.

## Conventions
- src-layout. Public API via `__init__.py`. Type-hint everything (mypy strict).
- Async by default (ADK + connectors async).
- Heavy deps (adk, mem0, telegram, pywa, textual, httpx…) imported lazily inside
  functions so package imports + unit-tests cleanly without model backend
  or secrets. New optional deps go in `pyproject.toml` dependency group.
- Tools return dicts (`{"status": "success"|"error", …}`), **never raise to
  model**, self-log one `tool_used` event per call via `done()` closure.
- Secrets via env / pydantic-settings (`config/settings.py`). Never hardcode keys;
  never put secret in gaia.yaml. Logs redacted best-effort — don't log secrets.

## Service lifecycle & DI
One Pythonic convention covers DI, Singleton, Lazy. `gaia.di.Container` (one per
`Gaia`) is **single composition root** — *every* build-once service
(`souls`, `users`, `tools`, `factory`, transcriber, memory, mcp/skill toolsets,
`connectors` registry) is a provider there. `Gaia.__init__` only builds
container, pulls handles under established names (`self.souls = …`):
`Gaia` is thin **facade + lifetime owner**, never second place to construct.
Add new service? It's a provider in `di.py`, not hand-built attr in `__init__`.
Backed by [`dependency-injector`](https://github.com/ets-labs/python-dependency-injector).
- **DI**: external collaborators (`Settings`, `ConfigSupplier`) reach
  container via `providers.Dependency` slots — wired by `Gaia.__init__`. One provider
  receives another *provider* (not its value) via `.provider` delegation — that's how
  `factory` gets lazy `mcp_toolsets`/`skill_toolsets` callables.
- **Singleton + Lazy**: every service is `providers.Singleton(factory, ...)`.
  Lazy by construction — nothing runs until first `container.X()`.
  Canonical example: `gaia.transcriber` builds Whisper-backed transcriber
  on first access, every later caller reuses same instance.
- **Hot-reloaded config**: `container.config` is `providers.Callable`, not
  Singleton, so each access re-reads `ConfigSupplier.current` — yaml edits still
  flow through.
- **No `global` mutable state** for service construction (ruff `PLW0603`
  enforces). No `@lru_cache` on factories — they hide lifetime, break test
  isolation. Sole sanctioned exception: `cli/_console.py` `console()` (stateless,
  no-arg, presentation-layer Rich singleton).
- **Cleanup is ours, not container's.** Services that own async resources
  (mcp toolsets, browser/shell process managers, skill toolsets) released by
  `Gaia.close()` in shutdown order; `Container.shutdown_resources` not used.
- **No `@inject`/`wire()`** (spiked + rejected, #146). `wire()` patches only
  *module-level* functions, so our `make_*`-closure tools/runners invisible to
  it, and it binds `Provide` markers at module (global) scope — clashing with
  per-`Gaia` container. Consumers read services explicitly (`gaia.X`, or pass one
  service they need); prefer narrowing tool's deps over threading whole `gaia`.