---
title: Browser
description: Drive a real headless browser - navigate, read, click, type, screenshot.
---

The `browser_*` tools let gaia drive a real browser to read and act on pages a plain fetch can't handle (JavaScript apps, logins, forms, things behind a click).

## What it can do

- `browser_navigate` - open a URL.
- `browser_snapshot` - read the page as a structured accessibility tree (what a fetch misses).
- `browser_click` / `browser_type` / `browser_press` - interact with elements.
- `browser_screenshot` - capture the page as an image (delivered to you automatically).
- `browser_scroll` / `browser_back` / `browser_get_images` / `browser_console` / `browser_dialog` / `browser_evaluate` - scroll, history, list images, read the console, answer JS dialogs, run JavaScript.

A `browser_screenshot` is sent to you as an image automatically - gaia doesn't need to `send_file` it.

## Two backends

The backend is `browser.backend` in `gaia.yaml`:

- **`native`** (default) - gaia's own `browser_*` tools driving the **Camoufox** anti-detect engine. gaia owns the whole surface: a stable tool schema, a per-request **SSRF guard** (it re-validates every redirect against private IPs), per-agent page isolation, and stronger anti-bot stealth. Set `browser.headless: "virtual"` on Linux to run a real browser on a virtual display (Xvfb) for the best anti-detection.
- **`mcp`** - hands the browser to Microsoft's **playwright-mcp** server via `bunx` (broader surface - tabs, PDF, network - but a third-party schema that drifts, and it needs `bun` on PATH; a missing runtime falls back to native).

The tradeoffs (URL safety, isolation, observability) are documented on `BrowserConfig` in `src/gaia/config/schema.py`.

## Safety

The native backend runs gaia's SSRF check (`validate_url`) on every navigation **and** every redirect, so a page can't bounce the browser to an internal address.
Each agent gets its own page, so souls don't share cookies or tabs.

Backend and flags are read once at startup - change them in `gaia.yaml` and `gaia restart`.
