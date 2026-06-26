---
title: Install, update, uninstall
description: "Get gaia running in one line; keep it up to date; remove it cleanly."
---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/Sho0pi/gaia/master/install.sh | sh
```

That one line:

1. ensures [`uv`](https://docs.astral.sh/uv/) and `git` are present (installs uv if missing),
2. creates a **self-contained venv at `~/.gaia/venv`** and installs gaia with **every feature**
   (`gaia[all]` — browser, OpenAI, MCP, web tools, memory),
3. sets up the browser runtime (**bun** for the default playwright-mcp backend + **Chromium** for the
   native fallback),
4. links the `gaia` command into `~/.local/bin`,
5. and, on a real terminal, walks you through **`gaia setup`** (pick a model + connectors).

macOS and Linux are supported. On Windows, install under **WSL**.

### Flags

`curl … | sh -s -- <flags>`:

| Flag | Effect |
|------|--------|
| `--ref <git-ref>` | install a specific branch / tag / commit |
| `--no-browser` | skip bun + Chromium |
| `--no-setup` | don't run `gaia setup` at the end |
| `--non-interactive` | no prompts (implies `--no-setup`) |

Everything lives under `~/.gaia` (the venv) and `~/.local/bin/gaia` (the launcher). Your config,
memory, and users are in `~/.gaia` (config in `~/.gaia/gaia.yaml`, secrets in `~/.gaia/.env`).

## Update

```bash
gaia update
```

Re-installs gaia from git into `~/.gaia/venv` and, if the daemon is running, restarts it so the new
code takes effect. `gaia update --ref <ref>` pins a specific version.

## Uninstall

```bash
gaia uninstall
```

Stops the daemon, removes the boot service (if installed) and the `gaia` launcher, then **asks**
whether to also delete `~/.gaia` (your config, memory, users, logs). By default your data is kept, so
a reinstall picks up where you left off. Non-interactive: `gaia uninstall --purge` deletes everything;
`--keep` keeps the data.
