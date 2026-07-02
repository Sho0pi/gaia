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
third-party code), wire it up, and tell you if it needs an API key.
It attaches on your **next message** - no restart.
If a key is needed, Gaia points you at where to create it and asks you to add it to `~/.gaia/.env`
(secrets never pass through the model).

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
      env_passthrough: ["TICKTICK_TOKEN"]   # var NAMES only - value goes in .env
      # tool_filter: ["create_task", "list_tasks"]   # optional: only these tools
      # tool_prefix: "tt"                             # optional: avoid name collisions

    # A remote server that authenticates with a Bearer token.
    # - name: ticktick
    #   transport: http
    #   url: "https://mcp.ticktick.com"
    #   headers: { Authorization: "Bearer ${TICKTICK_TOKEN}" }   # ${VAR} is read from .env
```

Secrets stay out of `gaia.yaml`: for stdio, list the env var **names** in `env_passthrough`; for a
remote server, reference them as `${VAR}` inside `headers`. Either way the value lives in
`~/.gaia/.env` (e.g. `TICKTICK_TOKEN=…`) - gaia reads it from the environment at launch, and any key
you add to `.env` is available this way.

You can also manage servers from the shell with `gaia tools`.

## Notes

- `stdio` servers need a launcher on PATH - `uvx`/`uv` (Python), `npx`/`node` or `bunx`/`bun` (Node).
  The installer sets these up; a missing launcher just skips that server (logged).
- An MCP server is third-party code. Only add servers you trust.
- A newly added server attaches on the next turn. If you edited `gaia.yaml` by hand while the daemon
  was already running, run `gaia restart` to be sure.
