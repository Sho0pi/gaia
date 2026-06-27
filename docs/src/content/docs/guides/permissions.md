---
title: Permissions
description: Roles, who becomes admin, and the ACL model.
---

## Roles

Every sender maps to one role:

- **admin** — full access: runs admin commands (`/acl`, `/model`, …), receives monitor DMs.
- **user** — chats with gaia normally; no admin commands.
- **guest** — gated: messages are silently dropped (never reach the model or memory) until an admin
  approves them. The local terminal (`cli`) is **always** admin and isn't configurable.

## Who becomes admin (onboarding)

You never have to look up your own id. On a fresh install with no admin yet, the **first person to
DM gaia becomes admin automatically** — on WhatsApp or Telegram. It's **DM-only**: a message in a
group never grants admin (so gaia being added to a group can't let a stranger take over). Once an
admin exists, new senders are `guest` and wait for approval.

**WhatsApp note:** scanning the QR at `gaia connect whatsapp` links **gaia's own** WhatsApp account
(the bot you chat with and add to groups) — *not* yours. You become admin by messaging gaia from
your phone after it's running.

At `gaia connect whatsapp` you can also **pre-allow other numbers** as `user`s so they skip the guest
gate.

## Managing access

- Approve / promote / list from chat or CLI: `gaia user`, `gaia acl` (see
  [Reference → CLI](/reference/cli/)).
- Declare an admin manually: `gaia setup admin --id whatsapp:<num>@s.whatsapp.net` (or
  `telegram:<id>`).
