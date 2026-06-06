---
name: adk-patterns
description: How to build ADK agents in godpy — LlmAgent, tools, SequentialAgent/ParallelAgent, session state, sub-agent delegation. Use when creating or editing any agent in src/godpy/god or src/godpy/agents.
---

# ADK patterns for godpy

- Root God agent = `LlmAgent` with `sub_agents=[...]` for LLM-driven delegation.
- Workflow agents: `SequentialAgent` (ordered, output → next input),
  `ParallelAgent` (independent tasks at once).
- Tools = plain Python functions with type hints + a docstring; ADK auto-wraps
  them. No manual schema.
- Shared data between agents = `Session.state` (the "whiteboard"). That is also
  godpy's short-term memory (`memory/short_term.py`).
- Delegation works only if each sub-agent's `description` is sharp — the parent
  routes by reading them. Keep descriptions task-specific.
- Always check `google/adk-samples` for the closest existing pattern before
  writing new orchestration.

## Reuse-first reminder
A new capability becomes an `AgentSpec` → persisted by `agents/registry.py`.
Look up an existing spec before creating one.

See `references/` for ADK doc + sample links.
