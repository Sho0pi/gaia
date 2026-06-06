---
name: a2a-patterns
description: How godpy exposes/consumes agents over A2A and persists AgentCards for reuse. Use when touching agents/factory.py or agents/registry.py.
---

# A2A in godpy

- A new subagent → an A2A AgentCard (name, description, url, skills). Build it
  with `to_agent_card()` in `agents/factory.py` (schema v0.3).
- `registry.py` stores specs as JSON in `agent_registry/`. Before creating an
  agent, look up an existing key → **reuse, don't recreate**.
- ADK has native A2A support:
  - Expose a godpy agent with ADK's A2A server helpers.
  - Consume a remote agent by adding it as a `sub_agent` via its AgentCard URL.
- Stay on A2A v0.3 card schema. Don't invent fields.

## Reference links
- A2A protocol: https://a2a-protocol.org/latest/
- ADK + A2A: https://google.github.io/adk-docs/a2a/
