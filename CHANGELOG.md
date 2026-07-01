# Changelog

All notable changes to gaia are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and gaia uses
[PEP 440](https://peps.python.org/pep-0440/) versions (`0.1.0a1` = first alpha). The version lives
in `src/gaia/__init__.py`; the GitHub Release for each tag is cut from the matching section below.

## [Unreleased]

### Added
- **Tappable answers** - when the agent asks a multiple-choice question (`ask_user`), Telegram
  renders inline buttons and WhatsApp a numbered list; the tap/number resumes the turn.
- **Telegram parity** - voice notes (transcribed locally), inbound and outbound media, a "typing…"
  indicator, and the `/` command menu.
- **Shell completion** - `gaia completion install` adds tab-completion for commands, options, and
  values (config keys, soul keys, task ids, users, capabilities). Installed on setup, refreshed on
  update, removed on uninstall.
- **Per-connector allow-list** - `connectors.<channel>.allow` in `gaia.yaml` pre-approves senders
  past the guest gate; number entry is forgiving (`+`, spaces, dashes).
- **GAIA.md customization** - a hand-edited `~/.gaia/GAIA.md` layers persona, house rules, and owner
  facts on top of the built-in prompt.
- **Prompt caching** - the system prompt splits into a cached static block and a per-session dynamic
  tail (date + user profile), so the stable part isn't re-sent every turn.
- **Docs** - a page per connector and per tool group, configuration + shell-completion guides, and a
  docs site restyled to match the marketing brand.

### Changed
- **Roles in `gaia.yaml`** - each role's capabilities (admin/user/guest) are laid out and editable
  under `roles.<role>.capabilities`; `cron` (self-service reminders) is now a default `user`
  capability, so reminders work without an admin grant.
- **`exec` allow-list widens** - `tools.exec.allowlist` adds to the built-in commands instead of
  replacing them (so allowing one extra tool no longer drops `git`/`python`/…).

### Fixed
- **Capability typos caught** - `/grant` and `/revoke` (chat and `gaia acl`) reject an unknown
  capability with the valid list instead of storing it silently.
- **`/approve` by name** - resolves a user by display name, shows the roster to pick from on no
  match, and can onboard a new person by number.
- **Relayed messages attributed** - `message_user` names who a message is from ("Itay: …") when it's
  sent on someone's behalf.

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
