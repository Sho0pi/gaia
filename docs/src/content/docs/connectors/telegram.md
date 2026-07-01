---
title: Telegram
description: Run gaia as a Telegram bot - voice, media, typing, a / menu, and tappable buttons.
---

Telegram is the richest chat channel: a bot you own, with voice transcription, media, a typing indicator, a `/` command menu, and tappable buttons.

## Set it up

1. Open Telegram, message **[@BotFather](https://t.me/BotFather)**, send `/newbot`, and name your bot.
2. BotFather replies with a token like `123456789:AAF…xyz`.
3. Connect it:

```bash
gaia connect telegram     # paste the token (or pass --token …)
gaia start                # bring the bot up (or `gaia restart` if the daemon is already running)
```

The token is a secret: it's stored in `~/.gaia/.env` as `GAIA_TELEGRAM_BOT_TOKEN` (0600) and **never** in `gaia.yaml`.
Then DM your bot - your first message makes you admin.

## What works

- **Text** - send and receive, with long replies split across messages past Telegram's 4096-char limit.
- **Voice notes** - transcribed locally (Whisper) and answered; the first one is slow while the model loads.
- **Inbound media** - photos, documents, and videos are downloaded so gaia can see/use the image.
- **Outbound media** - gaia sends real files (images, audio, video, documents) by type, not links.
- **Typing indicator** - a "typing…" status while gaia is working, refreshed until the reply lands.
- **The `/` menu** - commands are registered with Telegram (`setMyCommands`), so typing `/` autocompletes them.
- **Tappable buttons** - when gaia asks a multiple-choice question (the `ask_user` tool), the options render as inline-keyboard buttons; tap one and the keyboard is replaced with your pick.

## New senders + roles

A first-seen sender on Telegram is a **guest** by default, gated until an admin approves them.
Change the default with `connectors.telegram.default_role` (`guest` / `user` / `admin`) in `gaia.yaml`.

To use the same identity (and memory) as another channel, link them: `/link <your-id> telegram:<your-telegram-id>`.

## Configure it

```yaml
# ~/.gaia/gaia.yaml (the token is env-only - see above)
connectors:
  telegram:
    enabled: true
    default_role: guest   # role for a first-seen sender
```

All keys: [Reference → Config](/reference/config/); editing by hand: [Configuration](/guides/configuration/).

## Notes + limits

- It uses long-polling, so the daemon needs outbound internet; a brief network blip just retries.
- One bot token per gaia instance. The yaml has no `token` field by design - it's env-only.
- Inbound video/documents are downloaded but only **images** are fed to the model today (same as WhatsApp).
