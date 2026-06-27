---
title: Souls
description: The specialist subagents gaia forges, stores, and reuses.
---

A **soul** is a specialist subagent gaia delegates to. Instead of doing every task itself, gaia picks
the right soul for the job — or **forges a new one** when nothing fits — and keeps it for next time.
That's the core idea: gaia *builds the agents that do*, and gets sharper the more you use it.

## How it works

When a task needs a specialist, gaia runs the **soul-smith**, which makes one structured decision:
**reuse** an existing soul or **forge** a new one (name, instruction, model, tools, skills). Forged
souls are saved to the registry as JSON, so the next similar task reuses them — never recreated from
scratch. The chosen soul runs in its own **sandboxed workspace** with a fresh nested session; gaia
diffs the workspace before/after to capture what it produced.

You don't drive this by hand — it happens when you ask gaia for something a specialist should handle.
Souls stay **warm** between back-to-back delegations so a follow-up resumes instead of starting cold.

## Inspect + manage

```bash
gaia soul list             # every stored soul
gaia soul show <key>       # one soul's spec
gaia soul create <name>    # hand-author a soul
gaia soul edit <key>       # tweak its spec
gaia soul delete <key>
```

In chat, **`/soul`** shows the souls that are live right now (warm sessions). Full flags:
[Reference → CLI](/reference/cli/) and [Reference → Commands](/reference/commands/).

## Code map

| Concern | Module |
|---------|--------|
| Reuse-vs-forge decision (structured output) | `src/gaia/souls/smith.py` |
| The `delegate_to_soul` tool (sandboxed run, file diff) | `src/gaia/souls/delegate.py` |
| Running a soul turn / warm sessions | `src/gaia/souls/run.py`, `src/gaia/souls/sessions.py` |
| Spec → `LlmAgent`, persisted registry | `src/gaia/agents/{spec,factory,registry}.py` |
| `gaia soul` CLI | `src/gaia/cli/soul.py` |

Background: [Concepts → Missions](/concepts/missions/) (souls + the task board). Souls grow over time
via [Self-improvement](/guides/self-improve/).
