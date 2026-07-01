---
title: Troubleshooting
description: Common issues and how to fix them.
---

Quick fixes for the things that trip people up.
When in doubt, check `gaia status` and `~/.gaia/logs/daemon.log`.

## A connector is enabled but nothing happens

The daemon launches connectors at boot, so a newly enabled one needs a relaunch:

```bash
gaia restart
gaia status
```

If it still doesn't come up, the log says why (`grep -i telegram ~/.gaia/logs/daemon.log`).

## Telegram: "enabled but no token"

The token is read from the environment, not `gaia.yaml`.
Set it and restart:

```bash
gaia connect telegram    # writes GAIA_TELEGRAM_BOT_TOKEN to ~/.gaia/.env
gaia restart
```

## WhatsApp: needs a new QR / stopped receiving

The pairing lives in `~/.gaia/whatsapp.db`.
Re-pair by connecting again and scanning:

```bash
gaia connect whatsapp
gaia restart
```

Remember the QR links **gaia's own** WhatsApp account, not yours.

## Voice notes aren't transcribed

Voice needs the optional dependency group:

```bash
uv sync --all-extras   # installs faster-whisper (and the other extras)
```

The **first** voice note is slow while the model loads; later ones are fast.
Force a language or a bigger model under `voice:` in `gaia.yaml` if transcripts are off.

## "I'm treated as a guest"

A first-seen sender is a **guest**, gated until an admin approves.
On a fresh install the **first person to DM gaia becomes admin** automatically.
Otherwise an admin approves you, or raises the channel's `default_role`.
See [Permissions](/guides/permissions/).

## A config change didn't take effect

`gaia.yaml` hot-reloads on save - except **enabling a connector**, which needs `gaia restart`.
Check you edited `~/.gaia/gaia.yaml` (not a copy) and that the YAML is valid: `gaia config get <key>`.

## The browser tool fails

The default `native` backend needs its Camoufox build - `gaia update` fetches it.
The opt-in `mcp` backend needs `bun` on PATH; without it, gaia falls back to native.
See [Tools → Browser](/tools/browser/).

## Where are the logs?

`~/.gaia/logs/` - `daemon.log` (run log), `events.jsonl` (structured events), `system.log` (errors).
Bump `logging.level` to `DEBUG` in `gaia.yaml` for more detail.
