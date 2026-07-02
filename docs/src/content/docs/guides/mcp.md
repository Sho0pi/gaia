---
title: Add an MCP integration
description: Connect Gaia to external tools (TickTick, GitHub, …) via Model Context Protocol servers.
---

[Model Context Protocol](https://modelcontextprotocol.io) (MCP) servers extend Gaia with new tools -
a TickTick server to manage todos, a GitHub server to open issues, and so on.
Their tools attach to Gaia (and its souls) alongside the built-in ones.

## Just ask Gaia

The easy path: tell Gaia what you want, in chat.

> "Add the ticktick mcp so we can update my todos."

Gaia (an admin only) will research the server, confirm the exact one with you before adding (it's
third-party code), and wire it up. It attaches on your **next message** - no restart.

If it needs an API key, Gaia just asks you to **paste it** - the value goes into **your** secret
store and never passes through the model or the logs (it only sees a "saved" confirmation).
So the whole thing is one conversation:

> **you:** add ticktick mcp so I can manage my todos
> **gaia:** Found TickTick's official server. It needs a token - paste it here 👇
> **you:** *(paste)*
> **gaia:** Saved and wired. Ask me about your tasks anytime.

## Private by default

A server you add is **private to you** - it attaches only to your agent, and its token lives in your
own secret store (`~/.gaia/secrets/<you>.env`). So if you share Gaia with others, your ticktick stays
yours; they can't see or use it. Two people can each add the same integration with their own token,
and each only sees their own tasks.

Keyless utilities everyone should share (e.g. a time server) can be marked shared - just tell Gaia
"add it for everyone", or use `gaia mcp` (the CLI adds shared by default, being operator-level).

## Manage them manually

`/mcp` in chat, or the `gaia mcp` CLI:

```
/mcp                                list servers
/mcp add time uvx mcp-server-time   add a stdio server
/mcp remove time                    remove one
```
```bash
gaia mcp list
gaia mcp add time uvx mcp-server-time
gaia mcp remove time
```

## Or edit the config yourself

MCP servers live under `mcp.servers` in `~/.gaia/gaia.yaml`. Each is a local command (`stdio`) or a
remote URL (`http`/`sse`):

```yaml
mcp:
  servers:
    # A local (stdio) server launched on demand - Python via uvx, Node via npx/bunx.
    - name: ticktick
      transport: stdio
      command: uvx
      args: ["ticktick-mcp"]
      env_passthrough: ["TICKTICK_TOKEN"]   # var NAMES only - value goes in a secret store
      owner: itay                           # private to this user; omit/empty = shared
      # tool_filter: ["create_task", "list_tasks"]   # optional: only these tools
      # tool_prefix: "tt"                             # optional: avoid name collisions

    # A remote server that authenticates with a Bearer token.
    # - name: ticktick
    #   transport: http
    #   url: "https://mcp.ticktick.com"
    #   headers: { Authorization: "Bearer ${TICKTICK_TOKEN}" }   # ${VAR} resolved per-user
    #   owner: itay
```

Secrets stay out of `gaia.yaml`: for stdio, list the env var **names** in `env_passthrough`; for a
remote server, reference them as `${VAR}` inside `headers`. The value lives in the owner's secret
store `~/.gaia/secrets/<owner>.env` (a shared server falls back to the global `~/.gaia/.env`) - gaia
resolves it per-user at launch, so two people can hold their own token for the same server.

You can also manage servers from the shell with `gaia tools`.

## Notes

- `stdio` servers need a launcher on PATH - `uvx`/`uv` (Python), `npx`/`node` or `bunx`/`bun` (Node).
  The installer sets these up; a missing launcher just skips that server (logged).
- An MCP server is third-party code. Only add servers you trust.
- A newly added server attaches on the next turn. If you edited `gaia.yaml` by hand while the daemon
  was already running, run `gaia restart` to be sure.
