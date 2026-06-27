---
title: Scheduling
description: Have gaia run jobs on a schedule (cron).
---

gaia can run jobs on its own schedule — a **cron job** is a prompt gaia runs at set times, with the
result delivered to a chat. "Every morning at 8, summarize my unread and message me." "Every Monday,
draft the weekly report."

## How it works

Jobs live in a cron store; the background daemon's dispatcher fires each job when it's due, runs the
prompt (delegating to a soul if needed), and pushes the result to its target chat. Scheduling only
runs while the daemon is up — start it with `gaia start`.

You can also just *ask* in chat ("remind me every Friday to …") and gaia creates the job for you.

## Manage

```bash
gaia cron list                 # every scheduled job
gaia cron add ...              # add a job (schedule + prompt + target)
gaia cron enable <id>
gaia cron disable <id>
```

Full flags + the schedule syntax: [Reference → CLI](/reference/cli/).

## Code map

| Concern | Module |
|---------|--------|
| `gaia cron` CLI | `src/gaia/cli/cron.py` |
| Firing due jobs in the daemon | `src/gaia/missions/dispatcher.py` |
| Pushing the result to a chat | `src/gaia/missions/notify.py` |

Related: [Concepts → Missions](/concepts/missions/) (the task board cron jobs feed into).
