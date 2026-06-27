---
title: Connectors
description: Reach gaia on Telegram, WhatsApp, or the terminal.
---

A **connector** is how you talk to gaia. There are three: your **terminal** (built in), **Telegram**,
and **WhatsApp**. Connectors are thin I/O adapters — they hand each inbound message to the same gaia
core, so memory and souls work the same whichever channel you use.

Set up channels with `gaia connect`, then run gaia in the background with `gaia start`.

## First run (who becomes admin)

You never look up your own id. On a fresh install with no admin yet, the **first person to DM gaia
becomes admin automatically** (DM-only, so a group can't grab admin). Full rules:
[Permissions](/guides/permissions/).

## Terminal

```bash
gaia          # inline chat in your terminal — always available, always admin
```

Nothing to configure; great for trying things and local admin work.

## Telegram

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. `gaia connect telegram` (paste the token, or pass `--token`).
3. `gaia start`, then DM your bot — your first message makes you admin.

## WhatsApp

```bash
gaia connect whatsapp   # a QR appears — scan it
gaia start
```

The QR links **gaia's own WhatsApp account** (the bot you chat with and add to groups) — *not* yours.
You become admin by **messaging gaia from your phone** once it's running. At connect time you can also
pre-allow other numbers as regular users.

## Run it + check on it

```bash
gaia start     # run the connectors in the background (daemon)
gaia status    # is it up?
gaia stop      # stop the daemon
```

`gaia connect` with no channel opens an interactive menu to add or remove connectors.

## Code map

| Concern | Module |
|---------|--------|
| Connector adapters (cli / telegram / whatsapp) | `src/gaia/connectors/` |
| Resolve sender → user + role, route to a handler | `src/gaia/core/dispatch.py` |
| `gaia connect` / `start` / `status` CLI | `src/gaia/cli/` |

Full flags: [Reference → CLI](/reference/cli/). Roles + access: [Permissions](/guides/permissions/).
