# Gaia

AI agent inspired by openclaw, hermes-agent, picoclaw. The **Gaia** agent answers
simple things itself and delegates complex tasks to **souls** — specialist
subagents it forges once, **stores, and reuses** (never recreate). Two-tier
memory: short-term (ADK session) + long-term (mem0, grows day by day).

> **Renamed `godpy`/`God` → `gaia`/`Gaia` (PR #111).** Older issues, PRs, commit
> messages and branch names still say "godpy" or "God" — they mean this project /
> the root agent. The package is `gaia`, the CLI is `gaia`, config is `~/.gaia/gaia.yaml`,
> env vars are `GAIA_*`, and the root orchestrator lives in `src/gaia/core/` (class `Gaia`).

## Core rule: reuse, don't rebuild
Before writing anything custom, check for a proven library (many stars / used by
similar agent projects / official). Prefer integrating over reinventing. When
unsure which library, delegate to the `lib-researcher` subagent.

## Stack
- Python 3.11+, managed by **uv** (never pip / poetry / venv by hand).
- `google-adk` — agent runtime + LLM skeleton (LiteLLM wrapper for non-Gemini).
- `a2a-sdk` — agent↔subagent comms (ADK has native A2A support).
- `mem0ai` — long-term memory. ADK session `state` — short-term memory.
- `python-telegram-bot`, `pywa`, `neonize` — connectors; `textual` — chat TUI.

## Architecture (src/gaia/)
- `core/` — `agent.py` Gaia (root orchestrator), `handler.py` text↔ADK-Runner glue
  (one handler == one conversation), `dispatch.py` resolves an inbound sender to a
  `users.User` + role, gates guests, routes to a cached per-user handler, `plugins.py`
  tool-call logging.
- `users/` — cross-channel identities: `(channel, sender)` → canonical `user_id` + role
  (admin/user/guest), persisted to `~/.gaia/users.json`. Memory/sessions key off
  `user.id`, so one person shares memory across channels. Admins seeded from
  `config.admin`; others learned at first contact + managed by chat commands.
- `souls/` — the spawn/reuse loop: `smith.py` decides reuse-vs-forge (structured
  output); `delegate.py` is the root-only `delegate_to_soul` tool (nested Runner,
  sandboxed workspace, before/after file diff).
- `agents/` — `spec.py` AgentSpec, `factory.py` spec→`LlmAgent`, `registry.py`
  souls persisted as JSON → reuse, don't recreate.
- `tools/` — LLM-callable tools; `registry.py` name→callable, **on by default**,
  gated by `tools.<id>.enabled`. `fs/` is sandboxed per agent. → `new-tool` skill.
  Browser is dual-backend: `browser.backend` (default `mcp`) drives Microsoft's
  playwright-mcp via `bunx` (full tool surface, attached in `Gaia.mcp_toolsets`); set
  `native` for the built-in `browser_*` tools. Missing bun falls back to native. See
  `BrowserConfig` in `config/schema.py` for the SSRF/isolation/observability tradeoffs.
- `commands/` — in-chat slash commands (`/help`, `/reset`, …): one class per file,
  handled out-of-band, never reach the model. → `new-command` skill.
- `memory/` — `backend.py` builds mem0, `service.py` adapts it to ADK's
  `BaseMemoryService` (auto-ingest batching + `remember`/`load_memory` tools).
- `missions/` — the task board + engine. `store.py` (stdlib sqlite3/WAL, `~/.gaia/tasks.db`):
  `Task` rows + `TaskStore` CRUD/`ready_tasks`. Per-user `owner` (≠ `created_by` agent).
  `dispatcher.py` `MissionDispatcher` (P2) runs in the daemon: polls ready tasks → runs each
  on a soul via `souls/run.py:execute_decision` → posts result+artifacts → dependents consume
  upstream results; `notify.py` pushes the result to the task's notify target (chat→owner→
  cron default). Surfaced by `task_*`, `/tasks`, `gaia tasks`. Missions epic (#134, design
  `docs/missions-design.md`); status enum maps to A2A `TaskState` (P5 bridge).
- `connectors/` — thin I/O adapters only (cli TUI, telegram, whatsapp, whatsapp_web);
  each extracts the sender id + display name and calls a channel-bound `Dispatch`
  callable `(sender_id, name, text, send)` from `base.py` (the dispatcher resolves the
  user); `Send`/`Reply`/`Handler` also live there. → `new-connector` skill.
- `config/` — `settings.py` secrets (env only), `schema.py` gaia.yaml (hot-reloaded
  by `store.py`); the commented default file is **generated from the schema**
  (`scaffold.py`) — never hand-maintain a second copy.
- `providers/openai/` — Sign in with ChatGPT (OAuth device flow + Responses backend).
- Entry: the `gaia` CLI (`cli/`, Typer; `[project.scripts]`) → `app.py`
  (`run_cli` / `run` / `run_dev` / `run_auth`).
- `src/gaia/agents|souls` = gaia's RUNTIME agents. `.claude/agents/` = Claude Code
  DEV agents that help build gaia. Do not confuse them.

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
- `tests/system/` — touches something real: a model key (skipif-gated on
  `GEMINI_API_KEY`), a native lib (`importorskip`), or a live external resource.
  CI stays green without secrets because of the guards — never remove them.
- A feature isn't done until both tiers exist and are green.

## How to work here (senior R&D standard)
1. **Plan first.** For any new feature, study how ADK samples (`google/adk-samples`)
   or similar agent repos solved it before coding. Use `/plan-feature`.
2. **Match library idiom.** Read the lib's own source/examples; write in its style
   (ADK agent patterns, A2A card shapes). Don't invent conventions. Prefer ADK's
   **public** API — never reach into `_private` attributes of ADK objects.
3. **Test always** (see tiers above). Green tests or it isn't done.
4. **Self-review.** After a task, critique your own diff: what's weak, what you'd
   change next. Put it in the PR.
5. **New feature mid-task →** open a GitHub issue (`gh issue create`) with the right
   label + a proposed approach. Don't silently scope-creep.
6. **Finished feature →** open a PR (`gh pr create`) with summary, test evidence,
   and self-feedback. Never commit to master — branch first.

## Conventions
- src-layout. Public API via `__init__.py`. Type-hint everything (mypy strict).
- Async by default (ADK + connectors are async).
- Heavy deps (adk, mem0, telegram, pywa, textual, httpx…) imported lazily inside
  functions so the package imports and unit-tests cleanly without a model backend
  or secrets. New optional deps go in a `pyproject.toml` dependency group.
- Tools return dicts (`{"status": "success"|"error", …}`), **never raise to the
  model**, and self-log one `tool_used` event per call via a `done()` closure.
- Secrets via env / pydantic-settings (`config/settings.py`). Never hardcode keys;
  never put a secret in gaia.yaml. Logs are redacted best-effort — don't log secrets.

## Service lifecycle & DI
One Pythonic convention covers DI, Singleton and Lazy. `gaia.di.Container` (one per
`Gaia`) is the **single composition root** — *every* build-once service
(`souls`, `users`, `tools`, `factory`, transcriber, memory, mcp/skill toolsets,
the `connectors` registry) is a provider there. `Gaia.__init__` only builds the
container and pulls the handles under their established names (`self.souls = …`):
`Gaia` is a thin **facade + lifetime owner**, never a second place to construct.
Add a new service? It's a provider in `di.py`, not a hand-built attr in `__init__`.
Backed by [`dependency-injector`](https://github.com/ets-labs/python-dependency-injector).
- **DI**: external collaborators (`Settings`, `ConfigSupplier`) reach the
  container via `providers.Dependency` slots — wired by `Gaia.__init__`. One provider
  receives another *provider* (not its value) via `.provider` delegation — that's how
  `factory` gets the lazy `mcp_toolsets`/`skill_toolsets` callables.
- **Singleton + Lazy**: every service is a `providers.Singleton(factory, ...)`.
  They are lazy by construction — nothing runs until first `container.X()`.
  Canonical example: `gaia.transcriber` builds the Whisper-backed transcriber
  on first access, every later caller reuses the same instance.
- **Hot-reloaded config**: `container.config` is a `providers.Callable`, not a
  Singleton, so each access re-reads `ConfigSupplier.current` — yaml edits still
  flow through.
- **No `global` mutable state** for service construction (ruff `PLW0603`
  enforces). No `@lru_cache` on factories — they hide lifetime and break test
  isolation. Sole sanctioned exception: `cli/_console.py` `console()` (stateless,
  no-arg, presentation-layer Rich singleton).
- **Cleanup is ours, not the container's.** Services that own async resources
  (mcp toolsets, browser/shell process managers, skill toolsets) are released by
  `Gaia.close()` in shutdown order; `Container.shutdown_resources` is not used.
- **No `@inject`/`wire()`** (spiked + rejected, #146). `wire()` patches only
  *module-level* functions, so our `make_*`-closure tools/runners are invisible to
  it, and it binds `Provide` markers at module (global) scope — clashing with the
  per-`Gaia` container. Consumers read services explicitly (`gaia.X`, or pass the one
  service they need); prefer narrowing a tool's deps over threading the whole `gaia`.
