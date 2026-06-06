# Godpy

AI agent inspired by openclaw, hermes-agent, picoclaw. The **God** agent spawns
task-specific subagents, **stores them for reuse** (never recreate), and fine-tunes
them to the user over time. Two-tier memory: short-term (session) + long-term
(cross-session, grows day by day).

## Core rule: reuse, don't rebuild
Before writing anything custom, check for a proven library (many stars / used by
similar agent projects / official). Prefer integrating over reinventing. When
unsure which library, delegate to the `lib-researcher` subagent.

## Stack
- Python 3.11+, managed by **uv** (never pip / poetry / venv by hand).
- `google-adk` — agent runtime + LLM skeleton. Root agent in `src/godpy/god/`.
- `a2a-sdk` — agent↔subagent comms (ADK has native A2A support).
- `mem0ai` — long-term memory. ADK session `state` — short-term memory.
- `python-telegram-bot`, `pywa` — connectors.

## Architecture
- `god/agent.py` — orchestrator; routes a task to the best subagent.
- `agents/factory.py` — builds an ADK `LlmAgent` from an `AgentSpec`.
- `agents/registry.py` — persists specs as A2A AgentCards → reuse, don't recreate.
- `memory/short_term.py` (session state) + `memory/long_term.py` (mem0).
- `connectors/` — thin I/O adapters only; no business logic.
- `src/godpy/agents/` = godpy's RUNTIME agents. `.claude/agents/` = Claude Code
  DEV agents that help build godpy. Do not confuse them.

## Commands (always via uv, from repo root)
- Install: `uv sync --all-groups`
- Lint + fix: `uv run ruff check --fix . && uv run ruff format .`
- Types: `uv run mypy src`
- Test: `uv run pytest`
Do NOT `cd` into subdirs to run tools.

## How to work here (senior R&D standard)
1. **Plan first.** For any new feature, study how ADK samples (`google/adk-samples`)
   or similar agent repos solved it before coding. Use `/plan-feature`.
2. **Match library idiom.** Read the lib's own source/examples; write in its style
   (ADK agent patterns, A2A card shapes). Don't invent conventions.
3. **Test always.** Unit in `tests/unit/`, system/integration in `tests/system/`.
   New code ships with both. Green tests or it isn't done.
4. **Self-review.** After a task, critique your own diff: what's weak, what you'd
   change next. Put it in the PR.
5. **New feature mid-task →** open a GitHub issue (`gh issue create`) with the right
   label + a proposed approach. Don't silently scope-creep.
6. **Finished feature →** open a PR (`gh pr create`) with summary, test evidence,
   and self-feedback.

## Conventions
- src-layout. Public API via `__init__.py`. Type-hint everything (mypy strict).
- Async by default (ADK + connectors are async).
- Heavy deps (adk, mem0, telegram, pywa) imported lazily inside functions so the
  package imports and unit-tests cleanly without a model backend or secrets.
- Secrets via env / pydantic-settings in `config.py`. Never hardcode keys.
