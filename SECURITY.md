# Security Policy

## Supported versions

Gaia is pre-1.0 and ships from `master`. Security fixes land on `master`; please run the latest.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately through GitHub's [**Report a vulnerability**](https://github.com/Sho0pi/gaia/security/advisories/new)
flow (Security → Advisories). That opens a private advisory only the maintainer can see.

Please include:

- what the issue is and where (file / component),
- steps to reproduce or a proof of concept,
- the impact you think it has.

You'll get an acknowledgement as soon as possible, and a coordinated fix + disclosure once it's
confirmed.

## Scope notes

Gaia runs LLM-driven tools (shell exec, filesystem, browser) inside a per-agent sandbox and gates
them with an ACL capability model. Secrets live in `~/.gaia/.env` (never in `gaia.yaml` or the
repo). Reports about sandbox escapes, ACL bypasses, or secret leakage are especially welcome.
