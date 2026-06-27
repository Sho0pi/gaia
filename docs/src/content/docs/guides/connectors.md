---
title: Connectors
description: Reach gaia on Telegram, WhatsApp, or the terminal.
---

Set up channels with `gaia connect`. See [Reference → CLI](/reference/cli/) (`connect`, `start`, `status`).

## First run

```bash
gaia connect whatsapp   # scan the QR — this links gaia's OWN WhatsApp account (the bot)
gaia start              # run it in the background
```

Then **message gaia from your phone** — the first DM (on a fresh install with no admin) makes you the
admin automatically, no id to look up. Telegram is the same: `gaia connect telegram` with a BotFather
token, `gaia start`, then DM the bot. Full rules in [Permissions](/guides/permissions/).
