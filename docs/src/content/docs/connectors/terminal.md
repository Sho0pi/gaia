---
title: Terminal
description: The built-in CLI chat - always available, always admin.
---

The terminal connector is the built-in way to talk to gaia.
It needs no setup and no credentials, and the local operator is **always admin**, so it is the natural place to try things and to run admin commands.

```bash
gaia          # open an inline chat in your terminal
```

## Inline chat vs the daemon

`gaia` opens an **inline** chat that attaches to the running daemon over a local socket (`~/.gaia/gaia.sock`).
If the daemon isn't running it tells you to start it.

```bash
gaia start    # start the background daemon (connectors + scheduler)
gaia          # attach a terminal chat to it
gaia stop     # stop the daemon
```

This is why the terminal can't run *alongside* the background connectors in the same process - the inline chat owns the prompt.
Enable it on its own (the default for `gaia`), or run `gaia start` for the background channels and attach with `gaia`.

## What works here

Everything the core does: souls, memory, missions, slash commands, and every tool.
Replies are plain text and files are shown as paths (the terminal has no rich media), but the work itself is identical to any other channel.

It's the same gaia, so a fact you tell it in the terminal is remembered when you message it later on Telegram or WhatsApp.

## Drive gaia from the CLI

Beyond chatting, the `gaia` CLI inspects and manages everything from the terminal - souls, tasks, users, memory, skills, config.
See [Reference → CLI](/reference/cli/) for the full command set.
