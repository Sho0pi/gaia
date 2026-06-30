---
title: Files & shell
description: Read, write, and edit files, and run shell commands - all sandboxed to the workspace.
---

These are how a soul produces real deliverables: write code, edit a config, run a build, inspect output.
Everything is **sandboxed** to the agent's own workspace (`~/.gaia/agents/<agent>/workspace/`) - a tool can't read or write outside it.

## Files

- `fs_read` - read a UTF-8 text file.
- `fs_write` - create, overwrite, or append a text file (and make parent dirs).
- `fs_edit` - replace a unique snippet of text in a file (a precise edit, not a full rewrite).
- `fs_glob` - find files by glob (uses `fd` when installed).
- `fs_grep` - search file contents (uses `ripgrep` when installed).

They make up the `files` capability group.

## Shell

`exec` runs a single shell command in the workspace; `exec_poll` / `exec_list` / `exec_kill` manage long-running background processes (e.g. a dev server).

Shell access is the `shell` capability and is deliberately guarded - `exec` is not a raw shell:

- **Denylist (always on):** destructive commands are refused - `rm -rf /` or `~`, `dd` onto a device, fork bombs, `shutdown`/`reboot`, `curl … | sh` pipe-to-shell installs.
- **Allowlist mode (default):** only a curated set of dev tools runs (`ls`, `cat`, `git`, `python`/`python3`, `node`, `bun`/`bunx`, `pip`, `uv`, `pytest`, `grep`, `find`, …). Widen or change it in `gaia.yaml`.
- **One command per call:** chaining and substitution (`;`, `&&`, `||`, `|`, backticks, `$(…)`) are rejected.

The policy lives in `src/gaia/tools/shell/base.py`; tune the mode and allowlist under `tools.exec` in `gaia.yaml`.

## Capabilities

`files` and `shell` are powerful, so by default they go to trusted roles.
An admin grants them per user with `/grant <user> files` / `/grant <user> shell` (see [Permissions](/guides/permissions/)).
A soul inherits its owner's capabilities, so it can only write/run what that user is allowed.
