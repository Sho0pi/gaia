---
title: Web & search
description: Fetch a URL as clean markdown, and search the web.
---

Two lightweight tools for reading the web without driving a full [browser](/tools/browser/).

## `web_fetch`

Fetch a URL and return its main content as **markdown** (the readable article, stripped of nav and chrome).
Use it when you have a link and want the text; reach for the browser only when the page needs JavaScript or interaction.

It runs gaia's **SSRF guard**: the URL (and any redirect it follows) is validated against private/internal addresses, so a fetch can't be tricked into hitting your local network.

## `web_search`

Query the web and get back result titles, snippets, and links.
The engine is `tools.web_search.engine` in `gaia.yaml`:

- **`duckduckgo`** (default) - no API key, works out of the box.
- **`brave`** - higher quality, needs a `BRAVE_API_KEY` (set it with `gaia setup` or in `~/.gaia/.env`).

```bash
gaia setup search    # pick the engine, paste a key if needed
```

A typical flow: `web_search` to find the right page, then `web_fetch` (or the browser) to read it.

Both tools are in the `web` capability group, granted to regular users by default.

## Configure it

```yaml
# ~/.gaia/gaia.yaml (the brave key is env-only: BRAVE_API_KEY)
tools:
  web_search:
    engine: duckduckgo   # or 'brave'
```

Every tool is on by default; turn one off with `tools.<id>.enabled: false`. See [Configuration](/guides/configuration/).
