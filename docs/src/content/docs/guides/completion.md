---
title: Shell completion
description: Tab-complete gaia commands, options, and values - config keys, souls, tasks, and more.
---

The `gaia` CLI has **tab-completion** for commands and options, and - the advanced part - for
**values**: config keys and their choices, soul keys, task ids, user refs, tool ids, and capabilities.

## Enable it

`gaia setup` offers to install it. Or any time:

```bash
gaia completion install    # detects your shell (zsh/bash/fish) and writes the script
# then restart your shell (or source the file it names) to load it
```

`gaia completion show` prints the script instead, if you'd rather install it by hand.

## What completes

- `gaia <TAB>` / `gaia <group> <TAB>` - commands and subcommands.
- `gaia config get <TAB>` / `gaia config set <TAB>` - every dotted config key, walked from the schema.
- `gaia config set connectors.whatsapp.enabled <TAB>` - `true` / `false` (and the members of any choice field).
- `gaia soul show <TAB>` - your soul keys; `gaia task show <TAB>` - task ids (with titles).
- `gaia user role <TAB>` / `gaia acl grant <TAB>` - known users, roles, and capabilities.
- `gaia connect <TAB>` - channels; `gaia soul create --style <TAB>` - voices.

Completion is **best-effort**: a value source that's slow or missing simply yields no suggestions - it
never errors or hangs your shell.
