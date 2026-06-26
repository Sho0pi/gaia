---
title: Access control
description: "Who may talk to Gaia, and what each caller may do: pairing, roles, capabilities."
---

**Question.** Anyone can message a bot. How does Gaia decide *who* it answers, and *what* each
caller is allowed to make it do — without a flat "allow-list" that's easy to misconfigure?

Gaia's answer is two layers: **pairing** (who gets in at all) and a **capability ACL** (what they
may do once in). Both are driven by `users.json` + `gaia.yaml`, enforced in the runtime.

## Identities

A person is a `User` in `~/.gaia/users.json`: each `(channel, sender_id)` maps to a canonical
`user_id` + a `role`. One person who messages from both WhatsApp and Telegram resolves to the **same**
`user_id`, so their memory and permissions follow them across channels (`gaia.users`).

## Pairing: who gets answered

When a message arrives, `core/dispatch.py` resolves the sender to a `User`, registering a first-seen
sender at the connector's `default_role`:

- **Remote channels** (WhatsApp, Telegram) default a new sender to `guest`. **Guest messages are
  dropped** before the model or memory ever see them — nothing goes back over the wire. The sender
  waits for an admin to approve them out-of-band.
- **The local CLI** is **always `admin`**, regardless of config — the operator owns the machine, so a
  mis-set `default_role` can never lock them out of their own terminal.

This is *pairing*, not a static allow-list: strangers are gated by default, and an admin promotes
them with the `/user` commands (`/user approve …`, `/user role …`). There is no separate
`connectors.*.allow` field — roles + guest-gating are the access policy.

To seed the first admin(s), list their ids under the top-level `admin:` in `gaia.yaml`.

## Roles & capabilities: what they may do

Once a caller is in, every privileged action is gated by a **capability** — a token a role (or an
individual grant) holds (`gaia.acl`):

| Role | Holds |
|------|-------|
| `admin` | `*` — every tool and every command right (the owner) |
| `user` | a default capability set (general tools; **not** shell, **not** user management) |
| `guest` | nothing (and is gated at pairing anyway) |

Capabilities are grouped (`gaia.acl.groups`): a new tool joins a group once, and every role holding
that group gets it. A role's defaults can be overridden per role in `gaia.yaml`
(`roles.<role>.capabilities`), and an individual user can carry extra `grants` / `denies`
(`/grant`, `/deny`). The effective set is `role defaults ∪ grants − denies` (`gaia.acl.resolve`).

### Two enforcement points

- **Tools** — `ToolPermissionPlugin` (`core/plugins.py`) runs a hard `before_tool_callback` on the
  root agent *and* every soul: a tool call the caller's capabilities don't allow is denied before it
  runs. So a `user` literally cannot make Gaia call `run_command`, even if the model tries.
- **Commands** — each slash command declares a `capability` (`commands/base.py`); the same gate
  applies. `/forget` needs `manage_users`; the `/user` commands need it too. A caller without it gets
  a short refusal and nothing mutates.

An **unresolved** caller (cron jobs, the single-user/local path, tests) is trusted — there's no person
to scope to, and these run on the operator's own machine.

## Why not a static allow-list?

A per-connector "only these ids may message" list is redundant with guest-gating (strangers are
already dropped) and easy to get wrong (one typo locks everyone out). Gaia deliberately uses the
role + pairing model instead — the same approach as openclaw's pairing mode, with a finer-grained
capability ACL on top.
