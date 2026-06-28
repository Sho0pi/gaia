# Changelog

All notable changes to gaia are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and gaia uses
[PEP 440](https://peps.python.org/pep-0440/) versions (`0.1.0a1` = first alpha). The version lives
in `src/gaia/__init__.py`; the GitHub Release for each tag is cut from the matching section below.

## [Unreleased]

## [0.1.0a1] - 2026-06-28

First open alpha.

### Added
- **Souls** — gaia forges specialist subagents on demand, stores them, and reuses them; the
  soul-smith decides reuse-vs-forge. `gaia soul` / `/soul`.
- **Two-tier memory** — short-term ADK sessions + long-term mem0. Sessions are **durable**
  (survive restarts) and **windowed**; a conversation is **consolidated into long-term memory when
  it goes idle**, then cleared (human-like). Plus the `remember` tool.
- **Frictionless onboarding** — the first person to DM gaia (DM-only) becomes admin automatically;
  no id to look up. WhatsApp QR links gaia's own account; pre-allow others at connect.
- **Connectors** — terminal chat, Telegram (BotFather token), WhatsApp (QR). One identity, shared
  memory across channels.
- **Missions** — a task board + daemon dispatcher that runs souls, with cron **scheduling**.
- **Skills** — reusable `SKILL.md` playbooks loaded on demand (`gaia skill` / `/skill`).
- **Self-improvement** — gaia mines its own usage to propose new skills/souls (`gaia grow`).
- **Install / lifecycle** — one-line installer (`curl … | bash`), `gaia update` / `uninstall`,
  run-on-boot service, and `gaia report` for crash reports.
- **Docs** — docs.gaia-agent.com, plus an agent-readable layer (`AGENTS.md`, `/llms.txt`).

### Security
- exec is admin-only by default (ACL); destructive commands are denylisted in every mode.

[Unreleased]: https://github.com/Sho0pi/gaia/compare/v0.1.0a1...HEAD
[0.1.0a1]: https://github.com/Sho0pi/gaia/releases/tag/v0.1.0a1
