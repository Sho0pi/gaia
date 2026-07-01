---
title: Configuration
description: Edit gaia.yaml by hand - where it lives, how it hot-reloads, and the patterns.
---

Gaia's non-secret settings live in one file, **`~/.gaia/gaia.yaml`**.
It is **hot-reloaded**: edit, save, and the change is picked up without a restart (a *newly enabled connector* is the one exception - see below).

## Edit it

```bash
gaia config edit            # open gaia.yaml in $EDITOR
gaia config get llm.model   # read one value
gaia config set llm.model gemini-2.5-flash   # write one value
```

Or open `~/.gaia/gaia.yaml` in any editor - it's plain YAML.
The full, commented default (every key, generated from the schema) is in [Reference → Config](/reference/config/); this page explains how to work with it.

## Secrets go in `.env`, not here

Tokens and API keys **never** go in `gaia.yaml`.
They live in the environment, most simply in **`~/.gaia/.env`**:

```bash
# ~/.gaia/.env
GAIA_TELEGRAM_BOT_TOKEN=123456:AAF…
GEMINI_API_KEY=…
BRAVE_API_KEY=…
```

`gaia connect <channel>` writes these for you (0600).
In `gaia.yaml` a secret field like `telegram.token` is just a pointer - leave it `null` and set the env var.

## The shape

Top-level sections map to areas of gaia:

```yaml
llm:            # provider + model
connectors:     # telegram / whatsapp / cli
memory:         # long-term memory (mem0)
browser:        # the browser tools' backend
voice:          # inbound voice transcription
missions:       # the task board + dispatcher
```

A minimal file that turns on Telegram and picks a model:

```yaml
llm:
  provider: gemini
  model: gemini-2.5-flash

connectors:
  telegram:
    enabled: true
    default_role: guest   # a first-seen sender is gated until an admin approves
```

You only write the keys you want to change - everything else keeps its default.

## The override maps: on by default, tweak by id

Four sections are **keyed by id** and empty by default, because the thing they configure is already on.
You add an entry only to change one:

```yaml
tools:                    # every tool is attached by default
  web_search:
    engine: brave         # tweak one tool
  download_media:
    enabled: false        # turn one off

roles:                    # built-in role capabilities apply by default
  user:
    capabilities: [web, memory, files]   # give regular users more

agents: {}                # per-agent overrides (root agent's key is 'gaia')
commands: {}              # every slash command is on; disable with <name>.enabled: false
```

So "turn a tool off" or "give a role a capability" is a two-line addition, not a rewrite.

## Gotcha: enabling a new connector

The daemon starts its connectors when it boots.
Editing a *running* connector's settings hot-reloads, but switching one from `enabled: false` to `true` needs a restart to launch it:

```bash
gaia restart
```

See [Operations](/guides/operations/) for the daemon lifecycle and [Permissions](/guides/permissions/) for roles/capabilities.
