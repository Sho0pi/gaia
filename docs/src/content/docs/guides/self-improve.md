---
title: Self-improvement
description: How gaia mines its own usage to grow new skills, souls, and memories.
---

gaia is meant to **grow day by day**. The self-improve loop looks back over how you've used it and
proposes concrete improvements — a new [skill](/guides/skills/) for a task you repeat, a sharper
[soul](/guides/souls/), or a durable fact worth remembering.

## How it works

`gaia grow run` runs an **analyst** over recent usage and produces an *analysis report* of typed
proposals — skill, soul, and memory changes. Applying a report is **versioned with git** (under
gaia's state), so every change gaia makes to itself is reviewable and revertible.

Two modes (set in the `analysis` config block):

- **Autonomous** — gaia applies its own proposals (each git-committed). The daemon can also run a
  cycle periodically.
- **Review (HITL)** — proposals are queued for you to approve first (`analysis.autonomous = false`).

## Run + review

```bash
gaia grow run                  # analyze recent usage → proposals (model key needed)
gaia grow list                 # what gaia has changed about itself (from git history)
```

In chat, **`/grow`** shows the same self-change history. Full flags:
[Reference → CLI](/reference/cli/).

## Code map

| Concern | Module |
|---------|--------|
| The analyst + proposal types (skill/soul/memory) | `src/gaia/analysis/analyst.py` |
| Applying a report, git-versioned | `src/gaia/analysis/apply.py` |
| `gaia grow` CLI / `/grow` command | `src/gaia/cli/grow.py`, `src/gaia/commands/grow.py` |

Config: the `analysis` block — see [Reference → Config](/reference/config/).
