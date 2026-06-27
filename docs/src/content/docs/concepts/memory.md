---
title: Memory
description: "How Gaia remembers: two-tier (session + mem0)."
---
**Question.** How does Gaia remember anything — within a conversation, and across days
and channels — without leaking one person's memory into another's?

**Answer (short).** Three layers, two stores:

| Layer | What it is | Backed by | Lifetime |
|-------|-----------|-----------|----------|
| **Short-term** | the running conversation (recent turns) | ADK session state (`InMemorySessionService`) | the process / until `/reset` |
| **Long-term** | durable facts about the user, distilled day by day | **mem0** (Gemini extractor + embedder + a local Chroma vector store) | persists on disk, grows over time |
| **Initial / profile** | a compact "what I know about you" block put into the prompt at session start | *derived* from long-term + the task board by one LLM call — **not a third store** | recomputed each session |

Short-term is ADK's job; long-term is mem0's job; the profile is the bridge that makes
long-term memory *present* without the model having to go fetch it.

![memory flow](/diagrams/memory-flow.svg)

---

## 1. Short-term memory — the session

One **`GaiaHandler`** (`src/gaia/core/handler.py`) == one conversation. It owns an ADK
`Runner` over the shared **`DatabaseSessionService`** (`gaia.session_service`); the session
accumulates the turn events (user message, model responses, tool calls/results), and that event
history *is* the context the model sees on the next turn. This is what gives Gaia memory **within**
a conversation.

Key properties:

- **Durable.** Sessions live in `~/.gaia/sessions.db`, keyed on a stable `session_id`
  (`{user.id}:{channel}`), so a daemon restart **resumes** the exact conversation (#76).
- **Windowed replay.** `SessionWindowPlugin` (`core/plugins.py`) replays only the last
  `sessions.window_turns` (default 30) user turns to the model, so a long durable session can't
  bloat the context/tokens. The whole conversation stays on disk; older turns return via long-term
  memory recall.
- **Built once, reused.** The `Runner` is created on the first message (`_ensure_runner`,
  get-or-create on the durable session) and kept on the handler; later turns reuse it. It's rebuilt
  when `gaia.yaml` changes (config hot-reload, #60) — the durable session keeps the history.
- **Both sides are recorded.** The handler runs `run_async(..., yield_user_message=True)`
  so the user's own message is in the event stream too (ADK omits it by default). The
  user-role event is excluded from the *reply* path (so Gaia doesn't echo you) but is kept
  for consolidation (§3).
- **`/reset`** (`reset_session`) consolidates the conversation, then deletes the durable session →
  a clean slate. Long-term memory is untouched.

Short-term sessions need only `sessions.window_turns` / `sessions.idle_consolidate_minutes`; the
two-tier design is per `CLAUDE.md`.

---

## 2. Long-term memory — mem0

Long-term memory is **mem0**, adapted to ADK's `BaseMemoryService` contract by
`Mem0MemoryService` (`src/gaia/memory/service.py`) so it drops straight into the `Runner`.
The mem0 client itself is built by `build_mem0` (`src/gaia/memory/backend.py`).

mem0 is orchestration over three provider-agnostic pieces (all set in `gaia.yaml`'s
`memory:` block, all defaulting to a stock install):

- **LLM** — extracts durable facts from a conversation and decides add/update/no-op.
  Default: Gemini (reuses `settings.model` + `GEMINI_API_KEY`).
- **embedder** — vectorises facts for semantic search. Default: Gemini
  (`models/gemini-embedding-2`).
- **vector store** — holds the vectors. Default: a local **Chroma** at
  `~/.gaia/memory/chroma` (+ mem0's own SQLite history). Portable down to a Raspberry Pi.

Point any of the three at OpenAI / a local embedder / pgvector / qdrant without touching
code. **Secrets never go in `gaia.yaml`** — each provider reads its own env var inside
mem0, exactly like the agent model.

### What gets stored — extraction steering

mem0 runs with `infer=True` for the conversational path: it does **not** store raw
messages, it runs an LLM to extract *durable facts*. Left unguided, that extractor also
records the assistant's own actions ("a screenshot was captured", "task X was created") —
noise that crowds out real facts. So Gaia sets mem0's `custom_instructions`
(`EXTRACTION_INSTRUCTIONS` in `backend.py`) to keep **only durable facts about the user**
— identity, relationships and contacts, stable preferences, ongoing goals — and to
extract *nothing* from a message that only describes what the assistant did. Override per
install with `memory.extraction_instructions`.

---

## 3. Creating long-term memories — two write paths (+ one)

### Path A — idle consolidation (passive, the common case)

The conversation lives in a **durable, windowed session** (§1). gaia grows
long-term memory the way a person does: once a conversation goes quiet, it **consolidates** the
whole thing into mem0 — extracting only the important facts, with full conversation context — then
clears the session for a fresh, memory-informed start. No per-turn ingest.

1. After each turn, the handler (re)arms an idle timer for `sessions.idle_consolidate_minutes`
   (default 30; skipped when `memory.enabled` or `memory.auto_ingest` is off; slash commands never
   reach this path). The turn is already saved in the durable session — there's no separate buffer.
2. When the timer fires, `flush()` reads the **whole** session and calls
   `Mem0MemoryService.add_session_to_memory`, which maps the ADK events to mem0 `{role, content}`
   messages (`_events_to_messages`, ADK `model` → mem0 `assistant`) and calls
   `mem0.add(messages, user_id=…, infer=True)` off the loop — mem0 extracts/updates the important
   facts and dedups against the existing store. Then the session is deleted (a clean slate).
3. A **startup sweep** (`_consolidate_idle_sessions`) digests conversations that idled out while
   gaia was off, so nothing lingers un-consolidated across a restart.
4. There is **no shutdown flush**: a stop/crash just leaves the conversation in the durable session,
   to be consolidated on the next idle (or the startup sweep). `/reset` consolidates, then clears.

Because `yield_user_message=True` (§1), the session holds **both** the user's statements and Gaia's
replies — so mem0 can extract user-stated facts ("I'm vegetarian", "Grace is my girlfriend"), not
just things Gaia said. And because consolidation sees the full conversation, a fact's importance is
judged in context (the still-present turns), not from an isolated fragment.

### Path B — the `remember` tool (active, verbatim)

When something is worth keeping *exactly*, the model calls the **`remember`** tool
(`src/gaia/tools/remember.py`). It routes to `add_memory`, which writes with
`infer=False` — the fact is stored verbatim, no extraction. The model is told (in the root
prompt) to use `remember` when the user shares something durable.

### Path C — the self-improve loop (autonomous)

The growth loop (`gaia grow run`, `src/gaia/analysis/`) can also propose memory writes; an
approved `MemoryProposal` is applied via `apply_report` → `add_memory`. This is the same
write surface as `remember`, driven by analysis of usage rather than a single turn.

All three paths are **scoped by `user_id`** (§6).

---

## 4. Recalling long-term memory — two read paths

### Path A — the session-start profile ("initial memory")

The highest-leverage recall is **always-on**: when the handler builds the agent (session
start, and again on a config hot-reload), it runs **one LLM call** —
`distill_profile` (`src/gaia/memory/profile.py`) — that reads:

- the user's stored facts (`list_memories`, the full deduped set), and
- their **recent projects** (the task board: `TaskStore.list(owner=user_id)`),

and compresses them into a compact, **importance-ranked** block (≤ `memory.preload_limit`
bullets, default 20). The block is baked into the system prompt under `<USER_PROFILE>`, so
Gaia *always* knows who it's talking to without the model deciding to fetch anything.

Why one call at session start, not per turn, and not a stored file:

- The facts sit in the context window every turn regardless of *when* injected — LLMs are
  stateless — so per-turn injection costs the same tokens for no gain.
- A fact learned mid-session is already in the live history (§1); the next session
  re-distils a fresh profile. So freshness is covered without re-distilling each turn.
- Importance (not recency) is the selection axis: the model keeps "your name is Itay"
  verbatim and folds days of football chat into one line — recency-capping would evict the
  name once newer facts pile up.

Guardrails: `distill_profile` returns `None` (no model call) when memory is off or the user
has nothing stored, and falls back to the raw fact list if the profiler call errors — recall
never breaks a turn. Toggle with `memory.preload`.

### Path B — the `load_memory` tool (deep, on-demand)

For older or more specific details **not** in the profile, the model calls ADK's
**`load_memory`** tool, which routes to `Mem0MemoryService.search_memory`: a mem0 semantic
search filtered to the caller's `user_id`, returning the top `memory.recall_limit`
(default 5) hits as ADK memories. This is the agent-driven "go look it up" path; the root
prompt tells the model to use it when the profile doesn't already hold the answer.

### Bonus — contact resolution in `message_user`

`message_user` (`src/gaia/tools/message.py`) reuses recall to resolve a recipient named by
relationship/nickname: if "girlfriend" isn't a known user or a number, it `search_memory`s
the caller's memory for it and extracts a phone number — auto-sending on a single clear
match, asking otherwise. (Phone/WhatsApp-only today; channel-agnostic contacts → #206.)

---

## 5. Lifecycle of a turn

```
inbound text ─▶ GaiaHandler.__call__
  ├─ slash command?  ─▶ run it out-of-band, return (never touches the model/memory)
  ├─ _ensure_runner
  │    ├─ first message / config changed:
  │    │     _profile_block ─▶ distill_profile (1 LLM call: facts + recent projects)
  │    │     build_root_agent(profile=…)  ─▶ <USER_PROFILE> baked into the prompt
  │    └─ else: reuse the cached Runner (+ its session)
  ├─ run_async(yield_user_message=True)   [SessionWindowPlugin trims replay to window_turns]
  │    └─ model turn; may call  load_memory(query)  /  remember(fact)  ─▶ mem0
  ├─ emit reply (user-role event excluded so we don't echo the user)
  └─ _arm_idle_consolidate ─▶ (idle N min?) ─▶ flush ─▶ add_session_to_memory ─▶ mem0 ─▶ clear session
```

Other lifecycle points: **`/reset`** consolidates then clears the session; **shutdown** does
*nothing* to memory (the durable session is digested on the next idle / startup sweep); a
**`gaia.yaml` edit** rebuilds the Runner (and re-distils the profile) while keeping the session.

---

## 6. User isolation

Memory is strictly per-person, and shared across that person's channels:

- The `Dispatcher` (`src/gaia/core/dispatch.py`) resolves an inbound `(channel, sender)` to
  a canonical **`users.User`** and routes to a `GaiaHandler` cached per `(user, channel)`,
  built with `user_id = user.id`.
- Every mem0 operation — `add`, `search`, `get_all`, `delete` — is filtered by that
  `user_id`. The profile distiller and `load_memory` both pass the caller's `user_id`, so
  one person can never see another's facts.
- Because the key is the **canonical user id** (not the channel sender), the same person on
  WhatsApp and Telegram shares one memory; two different people on the same channel stay
  separate.
- The user store path is `Settings`-driven (`users_file`) so tests can't pollute the real
  store (#205).

---

## 7. Slash commands & config

In-chat surface:

- **`/remember <fact>`** — same as the tool: store a fact verbatim.
- **`/memory`** (aka `/memory`) — list everything stored for you (`list_memories`).
- **`/forget`** — wipe all of *your* long-term memory (`forget`); short-term untouched.

`gaia.yaml` `memory:` reference (`src/gaia/config/schema.py` → `MemoryConfig`):

| Field | Default | Meaning |
|-------|---------|---------|
| `enabled` | `true` | run long-term memory at all (off = session-only) |
| `auto_ingest` | `true` | passively consolidate a conversation on idle (Path A) |
| `recall_limit` | `5` | hits `load_memory` returns per search |
| `preload` | `true` | distil + inject the session-start profile |
| `preload_limit` | `20` | max bullets the profile keeps (importance-ranked) |
| `extraction_instructions` | `""` | override what mem0 extracts (empty = the built-in default) |
| `llm` / `embedder` / `vector_store` | gemini / gemini / chroma | mem0's three components |

`gaia.yaml` `sessions:` reference (`SessionsConfig`) — the short-term/consolidation knobs:

| Field | Default | Meaning |
|-------|---------|---------|
| `window_turns` | `30` | recent turns replayed to the model each message (token cap) |
| `idle_consolidate_minutes` | `30.0` | idle this long → consolidate the conversation + clear the session |

---

## 8. File map

| Concern | Module |
|---------|--------|
| Conversation glue, durable session, idle consolidation, profile hook | `src/gaia/core/handler.py` |
| mem0 ↔ ADK adapter (add/search/list/forget) | `src/gaia/memory/service.py` |
| Build the mem0 client + extraction steering | `src/gaia/memory/backend.py` |
| Session-start profile distillation | `src/gaia/memory/profile.py` |
| `remember` tool (verbatim write) | `src/gaia/tools/remember.py` |
| `load_memory` tool | ADK `load_memory_tool` (registered in `src/gaia/tools/registry.py`) |
| Prompt wiring + `memory_service` enabled-gate + profile injection | `src/gaia/core/agent.py` |
| Per-user routing / isolation | `src/gaia/core/dispatch.py` |
| `/remember` `/memory` `/forget` | `src/gaia/commands/` |
| Config schema | `src/gaia/config/schema.py` (`MemoryConfig`) |

> This document describes the memory subsystem as it lands across PRs #60 (config
> hot-reload reaching the live agent), #203 (ingest both sides + the session-start
> profile), #205 (user-store isolation) and #207 (extraction steering + memory-backed
> contact resolution).
