---
title: Customize Gaia (GAIA.md)
description: Shape Gaia's persona, rules, and what it knows about you - in one file.
---

Gaia's behavior comes from a built-in system prompt (identity, tool rules, safety). You customize it
with **`~/.gaia/GAIA.md`** - a plain-markdown file that layers your persona, house rules, and facts
about you **on top** of that base. It never disables tool use or safety, so you can't break Gaia by
editing it.

## Edit it

A commented template is created on first run. Open it in any editor:

```bash
$EDITOR ~/.gaia/GAIA.md
```

Write real lines under the headings; changes apply on your next message.

```markdown
## Persona
Warm, concise, a little witty. Skip the corporate tone.

## How to act
- Always confirm before spending money or messaging someone on my behalf.
- Default to metric units and a 24-hour clock.

## About me
I'm Itay, in Tel Aviv. Call me Itay.
```

The untouched template (all comments) injects nothing - Gaia only picks up text you actually write.

## How it fits together

Gaia's prompt has two parts:

- A **static** block - identity, tool rules, your voice, and your `GAIA.md` - identical for every
  message, so the model provider **caches it** and doesn't re-read it each turn (cheaper + faster).
- A **dynamic** tail - just the current time and what Gaia remembers about you - the only part that
  changes per message.

So editing `GAIA.md` re-warms that cache on your next session; day to day it costs nothing.

## GAIA.md vs. memory vs. gaia.yaml

- **GAIA.md** - persona, rules, and facts *you* set by hand.
- **Long-term memory** - facts Gaia *learns* about you over time (see [Memory](/concepts/memory/)); it
  can overlap GAIA.md, and GAIA.md wins for anything you state explicitly.
- **[gaia.yaml](/guides/configuration/)** - runtime settings (model, connectors, tools, roles), not
  persona.
