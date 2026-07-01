---
title: WhatsApp
description: Pair gaia's own WhatsApp account by QR - DMs and groups, voice and media.
---

WhatsApp connects gaia to a real WhatsApp account that you chat with and can add to groups.

## Set it up

```bash
gaia connect whatsapp     # a QR code appears in the terminal - scan it
gaia start
```

The QR links **gaia's own WhatsApp account** (the bot), *not* yours - scan it from the phone whose number gaia should use.
The session is saved to `~/.gaia/whatsapp.db`, so later runs reconnect without a new scan.

You become **admin** by messaging gaia from your phone once it's running.
At connect time you can also pre-allow other numbers as regular users.

## What works

- **Text** - send and receive; a reply quotes your message.
- **Voice notes** - transcribed locally (Whisper) and answered.
- **Inbound media** - images, videos, documents, and stickers are downloaded for gaia to use; location and contacts come through as a text proxy.
- **Outbound media** - gaia sends real files (image / video / audio / document) by type.
- **Presence** - gaia blue-ticks your message and shows "typing…" while it works.
- **Multiple-choice questions** - the `ask_user` tool renders as **numbered text** (reply with the number); native polls weren't reliable, so they're not used (Telegram has tappable buttons).

## Groups

Add gaia to a group and it engages only when **addressed** - @mentioned or someone replies to one of its messages - so it doesn't answer every line.
Tune this with `connectors.whatsapp.group_trigger` (e.g. `mention_only`) in `gaia.yaml`.
**Who** may trigger gaia is still the [access-control](/concepts/access-control/) system, not a per-group allow-list.

## New senders + roles

A first-seen sender is a **guest**, gated until an admin approves.
Set the default with `connectors.whatsapp.default_role`.
Link a number to an existing identity (shared memory) with `/link <your-id> whatsapp:<number>@s.whatsapp.net`.

## Configure it

```yaml
# ~/.gaia/gaia.yaml
connectors:
  whatsapp:
    enabled: true
    default_role: guest
    allow: ["+972 50-123-4567"] # pre-approve numbers past the gate (any format)
    show_active: true          # blue-tick + "typing…" while working
    group_trigger:
      respond_in_groups: true
      mention_only: true       # in groups, only when @mentioned or replied to
```

`allow` pre-approves specific senders as **users** without waiting for `/approve` - any number format works (`+`, spaces, dashes fine). It's additive; remove-to-revoke is `/approve <id> guest`. Everything else about who you are (linked channels, per-user permissions) stays in `users.json` - see [Access control](/concepts/access-control/).

All keys: [Reference → Config](/reference/config/); editing by hand: [Configuration](/guides/configuration/).

## Notes + limits

- This is the WhatsApp-Web (personal-account) backend via neonize; it reconnects automatically if the link drops.
- The QR uses gaia's number - treat that account as the bot, not your personal one.
- Inbound video/documents are downloaded but only **images** are fed to the model today.
