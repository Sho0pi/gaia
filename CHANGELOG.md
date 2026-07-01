# Changelog

All notable changes to gaia are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and gaia uses
[PEP 440](https://peps.python.org/pep-0440/) versions (`0.1.0a1` = first alpha). The version lives
in `src/gaia/__init__.py`; the GitHub Release for each tag is cut from the matching section below.

## [Unreleased]

## [0.1.0a1] - 2026-07-01

First open alpha.

### Added
- **Souls** - gaia forges specialist subagents on demand, stores them, and reuses them; the
  soul-smith decides reuse-vs-forge. `gaia soul` / `/soul`.
- **Two-tier memory** - short-term ADK sessions + long-term mem0. Sessions are durable (survive
  restarts) and windowed; an idle conversation is consolidated into long-term memory, then cleared
  (human-like). Plus the `remember` tool and a hand-edited `~/.gaia/GAIA.md` for persona, house
  rules, and owner facts.
- **Connectors** - terminal chat, Telegram, and WhatsApp (Cloud API + personal/QR); one identity with
  shared memory across channels. Voice notes (transcribed locally), inbound and outbound media, a
  "typing…" indicator, the `/` command menu, and tappable multiple-choice answers (`ask_user` renders
  inline buttons on Telegram, a numbered list on WhatsApp).
- **Access control** - roles (admin/user/guest) + capability groups and per-user grants, all laid out
  and editable in `gaia.yaml`; a per-connector allow-list with forgiving number entry; the first
  person to DM gaia becomes admin automatically; `/approve` resolves by name and can onboard a new
  person by number.
- **Tools** - sandboxed files and shell (denylist always, a widen-able allowlist), a browser (Camoufox
  anti-detect or playwright-mcp) with an SSRF guard, web fetch/search, image generation, media
  download, cron scheduling, and local serve + tunnel.
- **Missions** - a SQLite task board + in-daemon dispatcher that runs multi-step, multi-agent work on
  souls and pushes the result back to the owner.
- **Skills** - reusable `SKILL.md` playbooks loaded on demand (`gaia skill` / `/skill`); a self-improve
  loop mines usage to propose new skills and souls (`gaia grow`).
- **Prompt caching** - the system prompt splits into a cached static block and a per-session dynamic
  tail (date + user profile), so the stable part isn't re-sent every turn.
- **CLI + lifecycle** - `gaia` (Typer) with shell tab-completion, a one-line installer, `gaia update`
  / `uninstall`, a run-on-boot service, and `gaia report` for crash reports.
- **Docs** - docs.gaia-agent.com with a page per connector and per tool group, configuration and
  shell-completion guides, a brand-matched site, and an agent-readable layer (`AGENTS.md`, `/llms.txt`).

### Security
- exec is admin-only by default (ACL); destructive commands are denylisted in every mode.

[Unreleased]: https://github.com/Sho0pi/gaia/compare/v0.1.0a1...HEAD
[0.1.0a1]: https://github.com/Sho0pi/gaia/releases/tag/v0.1.0a1
