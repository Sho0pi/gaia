---
title: Running gaia
description: The daemon lifecycle, logs, and updating - local or on a always-on box.
---

Gaia runs as a background **daemon** that hosts the connectors, the cron scheduler, and the mission dispatcher.
You start it once and it stays up.

## Daemon lifecycle

```bash
gaia start      # start the daemon (connectors + scheduler + missions)
gaia status     # is it up? (pid, uptime)
gaia restart    # apply config that needs a relaunch (e.g. a newly enabled connector)
gaia stop       # stop it
gaia            # attach an inline terminal chat to the running daemon
```

The daemon launches every **enabled** connector at boot, so flipping one on in `gaia.yaml` takes a `gaia restart`.
Most other config [hot-reloads](/guides/configuration/) on save.

## What it runs

- **Connectors** - Telegram / WhatsApp / terminal (whichever are enabled).
- **Cron** - scheduled jobs the agent files for itself (`cron.enabled`).
- **Missions** - the task board dispatcher runs board tasks on souls (`missions.enabled`).
- **Analysis / Monitor** - optional self-improve and self-monitor loops (off by default).

## Logs

Everything lands in **`~/.gaia/logs/`**:

- `daemon.log` - the human-readable run log.
- `events.jsonl` - structured events (every `message_in`, `tool_used`, …), handy for grepping.
- `system.log` - errors + tracebacks.

Set the verbosity in `gaia.yaml`:

```yaml
logging:
  level: INFO   # DEBUG for more, WARNING/ERROR for less
```

## Updating

```bash
gaia update              # pull the latest, sync deps, fetch the browser engine, ready to restart
gaia update --ref <branch>   # deploy a specific branch/tag (e.g. to try a fix)
gaia restart
```

`gaia update` also fetches the Camoufox browser build the native browser tools need.

## On an always-on box (Pi, VPS)

The same commands work over SSH.
A common loop: `gaia update` (or `--ref <branch>`) → `gaia restart` → `gaia status`.
Pair WhatsApp/Telegram there once (the session persists to `~/.gaia/`), and gaia is reachable from your phone anywhere.

Stuck? See [Troubleshooting](/guides/troubleshooting/).
