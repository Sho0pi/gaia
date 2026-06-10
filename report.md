# godpy — Capability Gap Analysis & Roadmap

**Goal of this report:** measure godpy against the class of "full-fledged" personal
AI agents it's modelled on (openclaw, hermes-agent, zeroclaw/picoclaw) and lay out a
concrete, prioritized roadmap to close the gap. Written against `master` as of
2026-06-10, accounting for the open PRs and issues below.

> TL;DR — godpy already has a **solid, unusually clean spine**: a God→souls
> orchestrator, a real tool framework (fs/web + exec/browser in flight), two-tier
> memory, multi-connector I/O, hot-reload config, structured logging, and a strong
> test culture. What separates it from a "complete" agent is **not more tools** — it's
> four structural gaps: **(1) single-user / non-persistent sessions, (2) no
> agentic planning loop, (3) no MCP, (4) the skills "learn & grow" loop is unbuilt.**
> Everything else is incremental.

---

## 0. In-flight work (don't re-plan these)

**Open PRs**
- #75 web_search returns error dict (bug fix)
- #74 exec tool — foreground + background processes
- #71 browser tool — Playwright + accessibility snapshots (+ #72 media replies stacked on it, merged into its branch)
- #32 ask — human-in-the-loop clarification (LongRunningFunctionTool)

**Epic #64** tracks the hardening pass (#52–#63). Skills epic-ish cluster: #13–#19.
Souls cluster: #42 #44 #45 #58. Memory cluster: #35 #36 #37. Connectors: #2 #3 #6 #12 #30 #31.

This report focuses on what is **not** yet captured by an issue, or where the existing
issues need to be sequenced into a coherent whole.

---

## 1. What godpy has today (inventory)

| Area | Status | Notes |
|---|---|---|
| Orchestrator | ✅ | `God` LlmAgent; answers simple, delegates complex |
| Sub-agents ("souls") | ✅ basic | forge-or-reuse via `delegate_to_soul`, nested Runner, sandboxed workspace, persisted as JSON specs |
| Tools: fs | ✅ | read/write/edit/glob/grep, sandboxed per agent |
| Tools: web | ✅ | web_search (ddg), web_fetch (SSRF-guarded) |
| Tools: exec | 🟡 PR #74 | foreground + background processes, allow/denylist |
| Tools: browser | 🟡 PR #71 | Playwright, a11y snapshots, screenshots→WhatsApp |
| Tools: ask (HITL) | 🟡 PR #32 | LongRunningFunctionTool |
| Memory | ✅ basic | ADK session (short) + mem0 (long), auto-ingest batching |
| Skills | 🟡 partial | always-on folder skills only; no disclosure/authoring/growth |
| Connectors | ✅ | CLI (Textual TUI), Telegram, WhatsApp (neonize + pywa stub) |
| Config | ✅ | god.yaml hot-reload (mtime), env secrets, generated scaffold |
| Commands | ✅ | /help /reset /forget /remember /memories /agents /status /whoami |
| Logging | ✅ strong | system + events.jsonl, secret redaction, per-tool events |
| LLM providers | ✅ | Gemini native, OpenAI (LiteLLM + ChatGPT OAuth) |
| Tests | ✅ strong | ~238 unit + gated system, fakes, 89% coverage |

**This is a good foundation.** The architecture is clean (src-layout, lazy heavy deps,
pluggable backends, dict-return tools). The gaps below are about *completeness of the
agent*, not code quality.

---

## 2. The four structural gaps (highest leverage)

> **Now tracked as epics:** A → #81, B → #82, C → #83, D → #84 (with sub-issues
> #76–#80). A2A-decide quick win → #85.

### GAP A — Single-user, non-persistent sessions ⛔ (blocks "real product") — Epic #81
**Evidence:** `GodHandler` hardcodes `user_id="god-user"`, `session_id="god-session"`
(`god/handler.py:43,193`); every connector uses one shared handler. Sessions use
`InMemorySessionService` (`handler.py:62`, `souls/delegate.py:187`) — **the whole
conversation is lost on process restart**, and **all WhatsApp/Telegram senders share
one identity and one memory**.

**Why it matters:** openclaw/hermes are per-user, per-session, durable. A bot that
forgets on restart and merges every contact into one brain is a demo, not a product.

**What's needed:**
- Route connector sender → per-user `user_id` + per-chat `session_id` (issue #37 covers
  half of this — extend it to *session* routing + a handler-per-conversation registry).
- Swap `InMemorySessionService` for a durable ADK session service (SQLite/DB) so
  conversations survive restart.
- mem0 already scopes by `user_id` — wire the real id through and per-user memory works.

**Severity: critical. Related: #37. Net-new: durable session service.**

---

### GAP B — No agentic planning / control loop 🔴 — Epic #82
**Evidence:** God is a single-pass `LlmAgent` doing tool-calls; souls run one nested
turn. There is no explicit **plan → act → observe → reflect** loop, no task
decomposition, no self-correction/retry, no "todo list" the agent maintains across
steps.

**Why it matters:** the reference agents tackle multi-step goals ("build X, test it,
fix the failures, deploy") by *planning and iterating*. godpy can call several tools in
one model turn, but it doesn't persist a plan, track progress, or recover from a failed
step on its own. This is the difference between "tool-using chatbot" and "agent".

**What's needed (incremental):**
1. A **todo/plan tool** (write/update/complete tasks held in session state) so the model
   externalizes and tracks a multi-step plan — cheap, high impact.
2. A **reflection/retry** convention: on a failed tool result, the orchestrator
   instruction (and/or a wrapping loop) re-plans instead of giving up.
3. Longer term: a `SequentialAgent`/`LoopAgent` (ADK natives) wrapper for God so
   build-style tasks run an explicit iterate-until-done loop with a step budget.

**Severity: high. Net-new (no issue yet) — file an epic.**

---

### GAP C — No MCP (client or server) 🔴 — Epic #83
**Evidence:** `grep` finds no MCP usage. ADK ships `MCPToolset` (consume) and A2A
helpers (expose), both unused.

**Why it matters:** MCP is how the reference agents get *breadth* without writing every
integration — GitHub, Slack, databases, Playwright, filesystem servers, hundreds of
community tools. Without it, every capability is bespoke godpy code. It's the single
biggest force-multiplier available.

**What's needed:**
- **MCP client**: a config block (`mcp.servers: [...]`) that attaches `MCPToolset`s to
  God/souls — instantly gains the MCP tool ecosystem. (#70 already proposes this for the
  browser specifically; generalize it.)
- **MCP server** (later): expose godpy's own tools/souls over MCP so other agents use it.

**Severity: high. Related: #70 (browser-specific). Net-new: general MCP client block.**

---

### GAP D — Skills "learn & grow" loop is unbuilt 🟡 (godpy's *thesis*) — Epic #84
**Evidence:** CLAUDE.md's vision is "fine-tunes agents to the user over time" and
"long-term memory grows day by day." Today skills are **static always-on folders**.
The growth loop — observe usage → distill skills/memory → reuse — does not exist.

**Why it matters:** this is godpy's *differentiator* vs the references. It's currently
aspirational. The issues exist (#15 progressive disclosure, #16 skill tools/scripts,
#14 authoring, #13 clawhub download, #18 SQLite event store, #19 log-analysis agent)
but none are built, and they aren't sequenced.

**What's needed (sequence):** #18 (event store) → #19 (analyzer mines events into
candidate skills/memories) → #14 (agent writes new skills) → #15 (load on demand). That
chain *is* the "grows day by day" promise.

**Severity: high (strategic). Related: #13–#19. Needs sequencing into an epic.**

---

## 3. Secondary gaps (by capability domain)

### Tools breadth
- ✅ in flight: exec (#74), browser (#71), ask (#32).
- ❌ **apply_patch / multi-file edit** — fs_edit is single-string; complex code edits want a structured patch tool (openclaw has `apply_patch`).
- ❌ **git tool** (#45) — version each soul's workspace; track/persist deliverables.
- ❌ **todo/plan tool** (see GAP B).
- ❌ **image/vision input** — screenshots are produced but never *read back* by a vision model; no inbound image understanding.
- ❌ **scheduling/cron tool** — no way for the agent to run something later or recur.
- 🟡 web_search single engine (ddg only); no Google/Brave/Bing/Tavily backends.

### Sub-agents / multi-agent (souls)
- ✅ forge-or-reuse, sandboxed, persisted.
- ❌ **A2A is dormant** — `to_agent_card()` exists (`agents/factory.py`) but nothing serves or consumes cards over A2A. The "a2a-sdk" dep is unused at runtime. Either wire it (souls as real A2A agents) or drop the dep and the claim.
- ❌ single delegation depth (souls can't delegate); no **parallel** souls (ADK `ParallelAgent`).
- 🟡 sticky soul conversations (#44), per-task model selection (#42), double delegation path (#58) — all open, none done.

### Memory
- ✅ two-tier + auto-ingest.
- ⛔ **single-user** (see GAP A) + ❌ per-user routing (#37).
- ❌ **blocking** mem0 calls (#36 AsyncMemory) — ingest/recall block the turn.
- ❌ pgvector / scalable store (#35); chroma is local-only.
- ❌ no memory of *soul performance* (which soul did well) to inform future routing.

### Connectors / surfaces
- ✅ CLI, Telegram, WhatsApp.
- ❌ **no web chat UI** (only the ADK dev console) and **no HTTP/REST API** to drive God programmatically.
- ❌ Discord / Slack / email / voice.
- 🟡 WhatsApp business webhook unwired (#3); inbound media types unhandled (#6); Telegram config reload (#12); slash-command menu (#62).
- 🟡 neonize protobuf hack is fragile (#5).

### Reliability / ops
- ❌ **no durable sessions** (restart = amnesia) — see GAP A.
- ❌ no retry/backoff on transient model/tool errors (one friendly message, then drop).
- ❌ no rate-limit/queue handling beyond a polite reply.
- ❌ **no Dockerfile / deploy story / service unit**; install is uv-only, run is `python main.py`.
- ❌ no cost/token accounting; no tracing dashboard (events.jsonl is the raw substrate — #18 would turn it into something queryable).

### Security / multi-tenant
- 🟡 exec allow/denylist + fs sandbox are good, but **no container isolation** (the Docker `Spawner`/`Executor` seam exists but is unbuilt).
- ❌ **roles unwired** — `RoleConfig`, `admin`, connector `allow`/`default_role` are typed in the schema but **not enforced** anywhere. Anyone who can message the bot is effectively admin.
- ❌ no auth/identity verification on connectors beyond optional allow-lists.

### Human-in-the-loop / control
- 🟡 ask tool (#32) in flight; exec `ask` mode stubbed waiting on it.
- ❌ no approval gating for dangerous actions once ask lands (wire exec/browser → ask).

### Developer experience
- 🟡 CLI is argparse-with-positionals (#52 → Typer + `godpy` entry point).
- ❌ README is 41 lines; no architecture doc, no "how to add X" beyond the `.claude` skills, no deploy guide.

---

## 4. Prioritized roadmap

Ordered by **leverage ÷ effort**. Each phase is independently shippable.

### Phase 0 — finish the hardening pass (already scoped in #64)
Land #53/#75, #55, #56, #54, then #52/#59, then #57/#58/#60, then #61/#62/#63.
Merge the in-flight tool PRs (#71, #74, #32). *This is table stakes; do it first.*

### Phase 1 — make it a real product (GAP A) ⭐ highest priority
1. **Durable sessions**: replace `InMemorySessionService` with a DB-backed ADK session
   service. (small, unblocks everything)
2. **Per-user/session routing** (#37 extended): connector sender → `user_id`+`session_id`;
   a handler/runner registry keyed per conversation. Per-user memory falls out for free.
3. **Wire roles/admin** so the typed-but-dead access control actually gates.

### Phase 2 — agentic loop (GAP B) ⭐
1. **todo/plan tool** (session-state task list) — cheapest path to multi-step behaviour.
2. **retry/reflect** convention in the God + soul instructions.
3. **LoopAgent wrapper** for build-style tasks with a step budget.

### Phase 3 — MCP (GAP C) ⭐
1. **MCP client** config block → attach `MCPToolset`s (generalize #70). Instantly multiplies tool breadth.
2. Wire ask-gating (#32) in front of MCP/exec/browser for dangerous calls.

### Phase 4 — the skills growth loop (GAP D — the thesis)
Sequence: #18 event store → #19 analyzer → #14 authoring → #15 progressive disclosure
→ #13 clawhub. This delivers the "learns and grows day by day" promise.

### Phase 5 — surfaces & ops
Web chat UI + HTTP API; Dockerfile + deploy guide; retry/backoff; cost accounting;
async mem0 (#36); pgvector (#35); more connectors as needed.

### Phase 6 — depth
apply_patch/multi-file edit; git tool (#45); parallel souls; A2A actually served (or
drop the dep); vision input; scheduling/cron; more search backends.

---

## 5. Quick wins (high impact, < 1 day each)
- Durable session service swap (Phase 1.1) — kills the restart-amnesia bug.
- todo/plan tool (Phase 2.1) — unlocks multi-step behaviour with ~one tool file.
- MCP client config block (Phase 3.1) — one toolset wiring → huge tool breadth.
- Decide A2A: wire it or remove the dormant `a2a-sdk` dep + `to_agent_card` (honesty).
- README + architecture doc + Dockerfile — makes the project adoptable.

## 6. Honest one-liner
godpy is a **clean, well-tested agent skeleton with the God/souls idea mostly working**
and tools arriving fast. To be "full-fledged like openclaw/hermes," it most needs:
**durable per-user sessions, a planning loop, MCP, and the skills-growth loop it was
founded on.** Tools are the easy part — and they're already nearly done.

---

*Generated by deep analysis of `master` + open PRs/issues. Cross-reference the epic
#64 for the hardening sub-tasks; the four structural gaps above (A–D) are mostly not
yet captured as issues — recommend opening an epic per gap.*
