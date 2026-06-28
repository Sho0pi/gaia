---
title: Skills
description: Teach gaia reusable instructions it loads on demand (SKILL.md folders).
---

A **skill** is a folder of reusable instructions gaia pulls into context when it's relevant — a
`SKILL.md` file (plus any helper files) under your skills directory. Think of it as a playbook you
teach gaia once: "how to draft my weekly report", "how we deploy", "my writing style".

## How gaia uses them

The root agent carries an **on-demand skill toolset**: skills aren't all loaded into the prompt up
front (that would burn tokens). Instead gaia sees the available skills' names/descriptions and pulls
a skill's full text into context only when a task calls for it — progressive disclosure. Install a
skill and it's usable immediately, no restart.

## Author + manage

```bash
gaia skill list              # installed skills
gaia skill show <id>         # print a skill's SKILL.md
gaia skill new <id>          # scaffold a new SKILL.md folder to edit
gaia skill install <path>    # add a skill folder
gaia skill remove <id>
```

A skill is just `<skills_dir>/<id>/SKILL.md` — author it in any editor. In chat, **`/skill`** lists
what's available. Full flags: [Reference → CLI](/reference/cli/).

## Code map

| Concern | Module |
|---------|--------|
| Skill loading, `SKILL.md` parsing, on-demand toolset | `src/gaia/skills.py` |
| `gaia skill` CLI | `src/gaia/cli/skill.py` |

Skills can also **grow on their own** — gaia mines its usage and proposes new ones; see
[Self-improvement](/guides/self-improve/).
