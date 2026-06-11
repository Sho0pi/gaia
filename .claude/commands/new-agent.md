---
description: Create a new gaia runtime subagent (factory + registry + tests).
---

Create a gaia runtime subagent for: $ARGUMENTS

1. Check `agent_registry/` — does a reusable AgentSpec already exist for this?
   If yes, stop and report it. Reuse, don't recreate.
2. Define the `AgentSpec` and build it via `agents/factory.py`, following the
   `adk-patterns` skill.
3. Ensure its AgentCard is persisted via `agents/registry.py` (`a2a-patterns`).
4. Unit-test the factory/spec; system-test Gaia→subagent delegation.
5. Run the `test-runner` subagent. Then summarize + give self-feedback.
