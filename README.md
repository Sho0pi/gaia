# gaia

> I hate python — here is Gaia, the best agent you will ever use.

**Gaia** is an AI agent that spawns task-specific subagents on demand, **stores them for
reuse** (no recreating), and fine-tunes them to you over time. Inspired by openclaw,
hermes-agent, and picoclaw.

## Design

- **Reuse over rebuild.** Every capability leans on a proven library, never a from-scratch
  reinvention.
- **Gaia orchestrator** ([`src/gaia/core`](src/gaia/core)) routes a task to the best subagent.
- **Agent factory + registry** ([`src/gaia/agents`](src/gaia/agents)) builds an ADK agent
  for a new task and persists it as an A2A `AgentCard` so it is reused next time.
- **Two-tier memory** ([`src/gaia/memory`](src/gaia/memory)): short-term = ADK session
  state; long-term = [mem0](https://github.com/mem0ai/mem0).
- **Connectors** ([`src/gaia/connectors`](src/gaia/connectors)): Telegram, WhatsApp, CLI —
  thin I/O adapters only.
- **Config** ([`src/gaia/config`](src/gaia/config)): secrets come from env/`.env`
  ([`Settings`](src/gaia/config/settings.py)); everything else lives in a hot-reloaded
  `~/.gaia/gaia.yaml` ([`ConfigSupplier`](src/gaia/config/store.py)) — toggle connectors and
  edit settings without restarting. A commented default is scaffolded on first run.

## Stack

[google-adk](https://github.com/google/adk-python) · [a2a-sdk](https://a2a-protocol.org) ·
[mem0ai](https://github.com/mem0ai/mem0) ·
[python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) ·
[pywa](https://github.com/david-lev/pywa). Managed with [uv](https://docs.astral.sh/uv/).

## Develop

```bash
uv sync --all-groups            # install
uv run ruff check --fix .       # lint
uv run mypy src                 # types
uv run pytest                   # tests
```

See [CLAUDE.md](CLAUDE.md) for the full development workflow.
