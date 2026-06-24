---
title: Getting started
description: Install gaia, connect a channel, and have your first conversation.
---

Gaia runs locally and talks to you over Telegram, WhatsApp, or a terminal chat. You'll need
[uv](https://docs.astral.sh/uv/) and a model API key (Gemini by default; OpenAI / ChatGPT also
supported).

## Install

```bash
git clone https://github.com/Sho0pi/gaia.git
cd gaia
uv sync --all-groups
```

## Add a model key

Gaia reads secrets from `~/.gaia/.env`. Set your provider key:

```bash
echo "GEMINI_API_KEY=your-key" >> ~/.gaia/.env
```

Prefer OpenAI or your ChatGPT subscription? See [Reference → Config](/reference/config/) for the
`llm` block, or run `gaia llm auth openai` to sign in with ChatGPT.

## First chat (terminal)

```bash
uv run gaia
```

This opens an inline chat. Ask it to do something — "write me a haiku about the sea", "add a
task to buy milk", "run `echo hi`". Gaia answers simple things itself and **forges a soul** for
anything a specialist should handle.

## Connect a channel

To reach gaia from your phone, connect Telegram or WhatsApp, then run it as a background daemon:

```bash
uv run gaia connect          # interactive setup (Telegram token / WhatsApp QR)
uv run gaia start            # run in the background
uv run gaia status           # check it's up
```

See [Guides → Connectors](/guides/connectors/) for details.

## Next steps

- [Concepts → Missions](/concepts/missions/) — how souls and the task board work.
- [Concepts → Memory](/concepts/memory/) — what gaia remembers, and how.
- [Reference → CLI](/reference/cli/) — every `gaia` command.
- [Reference → Commands](/reference/commands/) — the in-chat slash commands.
