---
title: Tools
description: The things gaia can actually do - browse, search, run code, edit files, make images.
---

Tools are gaia's hands.
A soul or the root agent calls them to get real work done: open a web page, run a script, edit a file, generate an image, send you a file.

## How tools are governed

- **On by default.** Every registered tool is available unless you turn it off with `tools.<id>.enabled: false` in `gaia.yaml`. Some tools self-disable when a binary they need isn't installed (e.g. `fs_glob` needs `fd`).
- **Gated by capability.** Tools are grouped into ACL **capabilities** (`web`, `browser`, `files`, `shell`, `images`, `media`, …). A user's role grants a set of these, so a soul can only use the tools its owner is allowed. See [Access control](/concepts/access-control/) and [Permissions](/guides/permissions/).
- **Sandboxed.** File and shell tools are rooted in the agent's own workspace (`~/.gaia/agents/<agent>/workspace/`); one soul can't touch another's files.
- **Logged.** Every tool call is recorded as a `tool_used` event, so you can see exactly what gaia did.

The full list of tools and their config toggles is generated from the code in [Reference → Config](/reference/config/).

## The tool groups

- **[Browser](/tools/browser/)** - drive a real headless browser: navigate, read the page, click, type, screenshot.
- **[Web & search](/tools/web-search/)** - fetch a URL as markdown, and search the web.
- **[Files & shell](/tools/files-and-shell/)** - read/write/edit files and run shell commands, all in the workspace.
- **[Images & media](/tools/media-and-images/)** - generate images, download videos/audio, and deliver files to you.

Memory tools (`remember`, `load_memory`) are covered in [Memory](/concepts/memory/); scheduling (`cron`, `task_*`) in [Scheduling](/guides/scheduling/); delegation (`delegate_to_soul`) in [Souls](/guides/souls/).
