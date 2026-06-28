---
title: Getting started
description: Install gaia, connect a channel, and have your first conversation.
---

Gaia runs locally and talks to you over Telegram, WhatsApp, or a terminal chat. You need a model
API key (Gemini by default; OpenAI / ChatGPT also supported).

## Install

```bash
curl -fsSL https://gaia-agent.com/install.sh | bash
```

One line: installs a self-contained gaia (every feature), links the `gaia` command, and walks you
through `gaia setup` (pick a model + connectors). macOS + Linux (Windows: use WSL). Details +
update/uninstall: [Guides → Install](/guides/install/). Prefer source? See [From source](#from-source).

## Add a model key

If you skipped setup, point gaia at a provider — secrets live in `~/.gaia/.env`:

```bash
echo "GEMINI_API_KEY=your-key" >> ~/.gaia/.env
```

Prefer OpenAI or your ChatGPT subscription? Run `gaia model` to pick a provider and sign in with
ChatGPT, or see [Reference → Config](/reference/config/) for the `llm` block.

## First chat (terminal)

```bash
gaia
```

This opens an inline chat. Ask it to do something — "write me a haiku about the sea", "add a
task to buy milk", "run `echo hi`". Gaia answers simple things itself and **forges a soul** for
anything a specialist should handle.

## Connect a channel

To reach gaia from your phone, connect Telegram or WhatsApp, then run it as a background daemon:

```bash
gaia connect          # interactive setup (Telegram token / WhatsApp QR)
gaia start            # run in the background
gaia status           # check it's up
```

Then DM gaia — your first message makes you admin. See [Guides → Connectors](/guides/connectors/).

## From source

For development (uses [uv](https://docs.astral.sh/uv/)):

```bash
git clone https://github.com/Sho0pi/gaia.git && cd gaia
uv sync --all-extras --all-groups
echo "GEMINI_API_KEY=your-key" >> ~/.gaia/.env
uv run gaia
```

## Next steps

- [Concepts → Missions](/concepts/missions/) — how souls and the task board work.
- [Concepts → Memory](/concepts/memory/) — what gaia remembers, and how.
- [Reference → CLI](/reference/cli/) — every `gaia` command.
- [Reference → Commands](/reference/commands/) — the in-chat slash commands.
