---
title: Shell completion
description: Tab-complete gaia commands, options, and values - config keys, souls, tasks, and more.
---

The `gaia` CLI has **tab-completion** for commands and options, and - the advanced part - for
**values**: config keys and their choices, soul keys, task ids, user refs, tool ids, and capabilities.

## It's on by default

Completion is installed **automatically**: `gaia setup` (run by the installer) writes it for your
shell, and every `gaia update` refreshes it. Just **restart your shell** once to load it.

Manage it by hand any time:

```bash
gaia completion install     # (re)install for the detected shell (zsh/bash/fish)
gaia completion show        # print the script instead, to install it manually
gaia completion uninstall   # remove it
```

`gaia uninstall` also removes the completion scripts.

## What completes

- `gaia <TAB>` / `gaia <group> <TAB>` - commands and subcommands.
- `gaia config get <TAB>` / `gaia config set <TAB>` - every dotted config key, walked from the schema.
- `gaia config set connectors.whatsapp.enabled <TAB>` - `true` / `false` (and the members of any choice field).
- `gaia soul show <TAB>` - your soul keys; `gaia task show <TAB>` - task ids (with titles).
- `gaia user role <TAB>` / `gaia acl grant <TAB>` - known users, roles, and capabilities.
- `gaia connect <TAB>` - channels; `gaia soul create --style <TAB>` - voices.

Completion is **best-effort**: a value source that's slow or missing simply yields no suggestions - it
never errors or hangs your shell.
