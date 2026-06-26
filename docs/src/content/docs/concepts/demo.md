---
title: Demo walkthrough
description: See gaia run end-to-end.
---
A repeatable, end-to-end walkthrough that proves the MVP works **from a user's seat** — not
unit tests, the real app. Run it on **WhatsApp Web** (the channel with the most to show:
voice in/out, group gating, proactive push) and tick the checklist at the end.

> Scope: the six pillars — memory, voice, schedules, web/browser, multi-agent missions,
> approval gates. Known gaps are listed at the bottom; don't demo those.

## Setup (once)

1. Configure `~/.gaia/gaia.yaml`:
   ```yaml
   connectors:
     whatsapp:
       enabled: true
   voice:
     enabled: true            # whisper speech-to-text in
     reply_with_voice: true   # edge-tts text-to-speech out (voice-in → voice-out)
   memory:
     enabled: true
     auto_ingest: true
   missions:
     approval_classes: [spend, book, send_as_me, destructive]
     max_tasks: 20            # drop to ~4 only for the runaway-cap demo (#7)
   cron:
     deliver:                 # where scheduled results land when fired off-chat
       channel: whatsapp
       chat: "<your-whatsapp-jid>"
   ```
2. Install deps: `uv sync --all-extras --all-groups` (whisper, edge-tts, browser). For the browser:
   either `bun` on PATH (default playwright-mcp backend) or
   `uv run playwright install chromium` (native). edge-tts needs network, no `espeak-ng`.
3. Auth a model: `uv run gaia model` (or put a Gemini key in `~/.gaia/.env`).
4. Start the daemon and pair: `uv run gaia start`, scan the QR with WhatsApp once.
   Confirm health: `uv run gaia status` and `uv run gaia doctor`.
5. Watch while you demo (separate terminals):
   - `uv run gaia logs --follow --events`
   - `uv run gaia task list` (re-run to see the board move)

## Scenarios

Run in order. Each is **prompt → expect → pass**. Prompts are sent as WhatsApp messages to
your paired number.

### 1. Memory — "it knows me"
- Say: *"Remember my girlfriend's name is Grace and I'm cutting on an A/B gym split."*
- Then `/reset`, then: *"What's my girlfriend's name and my training split?"*
- **Pass:** recalls **Grace** + the **A/B split**; `/memory` lists both.

### 2. Voice — "talk to it, it talks back"
- Send a **voice note**: *"give me a one-paragraph summary of today's AI news."*
- **Pass:** it transcribes (whisper, see logs) and replies **as a voice note** (edge-tts).
  A typed question still gets a typed reply.

### 3. Schedule — "it reaches out on its own"
- Say: *"In 2 minutes, send me a one-line hello."* (or *"every day at 8am send me a 3-bullet AI brief"*).
- **Pass:** `uv run gaia cron list` shows the job; at fire time the message is **pushed**
  proactively to the chat (you didn't message first).

### 4. Web + browser — "it can act on the live web"
- Say: *"Find a well-reviewed ramen spot near Marina Bay Singapore and screenshot its page."*
- **Pass:** `web_search`/`web_fetch` fire (logs) and a **screenshot image** arrives in chat.

### 5. Missions (the headline) — "real work done, with brakes"
- Say: *"Build a one-page website for my gym A/B plan — design the actual program first, then build the site from it."*
- **Pass:** the dispatcher forges a trainer soul + a frontend soul; a soul **consults** an
  expert or files a **subtask and yields**, and the parent re-runs with the result
  (`running → blocked → … → done`). The site is **presented once** (screenshot + summary);
  internal steps are not pushed. Check `uv run gaia task list` (the tree) and
  `~/.gaia/agents/<soul>/workspace/` (the files). Events: `task_blocked_on_children`,
  `consult_soul`.

### 6. Approval gate — "it pauses for me on risky actions"
- Say: *"Research a TLV→NYC flight next Tuesday and book the cheapest one."*
- **Pass:** research runs; the booking task **parks** and pushes
  *"⏸ … needs approval (spend) … /task approve `<id>`"*. Reply `/task approve <id>` → it
  proceeds; `/task reject <id>` → it fails and you get a notice.
- **Restart proof:** while it's parked, `uv run gaia restart`, then `/task` — still
  `awaiting_approval`; approve → it runs. (The board survives reboots.)

### 7. (optional) Runaway cap — set `missions.max_tasks: 4` first
- Say: *"Plan and execute a full product launch: landing page, blog, email sequence, social posts, press release."*
- **Pass:** the mission **pauses** after the cap: *"⏸ Mission … paused — task cap reached …"*.

## Ready checklist

- [ ] 1 — memory recall across `/reset`
- [ ] 2 — voice-in → voice-out round trip
- [ ] 3 — proactive scheduled push (no message from you)
- [ ] 4 — screenshot/media delivered from a live page
- [ ] 5 — multi-soul mission: forge + consult/subtask + re-run + single deliverable push
- [ ] 6 — approval park → approve/reject → runs; survives a restart
- [ ] no crashes in `~/.gaia/logs/errors.log`; no stuck `running` tasks after a restart

One grep to confirm each pillar's event fired:
```
grep -E "memory_updated|media_out|task_awaiting_approval|task_blocked_on_children|consult_soul|mission_paused" \
  ~/.gaia/logs/events.jsonl
```

**Green** = all six ticked, `errors.log` clean, restart leaves no stuck `running` tasks →
MVP-ready. A failing scenario is a concrete bug to file (label it by pillar), not a vague
"not ready".

## Known gaps (don't demo these)

- WhatsApp **Business** (Cloud API) inbound webhook isn't wired (#3) — use WhatsApp **Web**.
- No `/schedules` chat command yet — manage schedules with the `cron` tool in chat or
  `gaia cron …` on the CLI.
- Mission **planner / re-plan-on-stall** is P4 (#130): Gaia decomposes via `task_plan`, but
  doesn't yet auto-re-plan a stalled mission.
- **A2A external agents** is P5 (#131); **token/cost budget** (#133) and **session-resume**
  (#132) are follow-ups — long missions are bounded by `max_tasks`/`max_hours`, not tokens.
- Souls run on the configured model, so a deep mission spends real tokens — keep the caps
  modest for demos.
