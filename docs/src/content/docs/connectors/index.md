---
title: Connectors
description: How you reach gaia - terminal, Telegram, or WhatsApp.
---

A **connector** is how you talk to gaia.
There are three: your **terminal** (built in), **[Telegram](/connectors/telegram/)**, and **[WhatsApp](/connectors/whatsapp/)**.

Connectors are thin I/O adapters.
Each one extracts the sender and the text, hands it to the same gaia core, and sends the reply back.
Memory, souls, missions, and slash commands all work the same whichever channel you use - and because one person is [one identity across channels](/concepts/access-control/), your WhatsApp and Telegram share the same long-term memory.

## Set one up

```bash
gaia connect            # interactive menu: add or remove a channel
gaia connect telegram   # or go straight to one
gaia start              # run the enabled connectors in the background (the daemon)
```

`gaia connect` writes the channel's secret to `~/.gaia/.env` (0600, never to `gaia.yaml`) and flips `connectors.<name>.enabled` in `gaia.yaml`.
The terminal needs no setup at all.

## Who becomes admin

You never have to look up your own id.
On a fresh install with no admin yet, the **first person to DM gaia becomes admin automatically** (DM-only, so a group can't grab admin).

After that, a new sender on any channel is a **guest** by default and is gated until an admin approves them.
Change the default for a channel with `connectors.<name>.default_role` (`guest` / `user` / `admin`).
The full model is in [Access control](/concepts/access-control/) and [Permissions](/guides/permissions/).

## Run it + check on it

```bash
gaia start     # run the connectors in the background (daemon)
gaia status    # is it up?
gaia restart   # apply a config change / bring up a newly-enabled connector
gaia stop      # stop the daemon
```

A newly-enabled connector starts on the next `gaia restart` (the daemon launches connectors at boot).

## Per-channel guides

- **[Terminal](/connectors/terminal/)** - the built-in `gaia` chat, always available, always admin.
- **[Telegram](/connectors/telegram/)** - a bot you create with @BotFather; voice, media, typing, a `/` menu, tappable buttons.
- **[WhatsApp](/connectors/whatsapp/)** - pair gaia's own account by QR; works in DMs and groups.

Full CLI flags: [Reference → CLI](/reference/cli/).
The connector code lives in `src/gaia/connectors/`; sender-to-user routing is `src/gaia/core/dispatch.py`.
