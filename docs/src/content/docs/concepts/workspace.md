---
title: Workspace
description: Per-soul vs shared files, and who sees what.
---
**Question.** Should every agent (Gaia + each soul) keep its own workspace, should they all
share one, or should it be hierarchical — Gaia sees all workspaces, each soul sees only its
own? Workspaces hold tmp data and screenshots today, and will later hold per-soul state
(`soul.md`-style files, notes, caches).

**Recommendation (short).** Adopt the **hierarchical model you proposed**: keep one
workspace per soul, give the root orchestrator (Gaia) read-write access to *all* of them,
and keep souls confined to their own. It is a small delta over today's code, it fixes a
real bug we ship right now (Gaia cannot read the files a soul produced), and it is the
direction the prior art is converging on.

---

## 1. What gaia does today

- Every `fs_*`/`exec`/`browser_screenshot` call sandboxes per `tool_context.agent_name`
  (`tools/fs/base.py:sandbox_for`): primary root `~/.gaia/agents/<name>/workspace` plus a
  scratch root `/tmp/gaia/<name>`. Path escapes (`..`, absolute, symlink) are
  realpath-rejected.
- So the root agent (`gaia`) and each soul get **fully isolated** workspaces. No agent can
  see any other agent's files.
- `delegate_to_soul` bridges the gap only *descriptively*: it snapshots the soul's
  workspace before/after the run and returns the **path + list of changed files** — not
  the contents (`souls/delegate.py:_snapshot/_changed`).

**The consequence we already feel:** after a delegation, Gaia knows *that*
`~/.gaia/agents/web_designer/workspace/index.html` exists but **cannot `fs_read` it** —
its sandbox roots don't include soul workspaces. It can't show the user a deliverable,
verify it, summarize it, or hand it to another soul. Today's design is isolation without
an owner.

## 2. Prior art

| System | Workspace model | Subagent access | Notes |
|---|---|---|---|
| **openclaw** | One workspace per agent *profile* (`~/.openclaw/workspace[-profile]`), holding the identity files (`SOUL.md`, `AGENTS.md`, `MEMORY.md`, …) | Default subagents **inherit the parent's workspace** (shared) | Open feature request [#29372](https://github.com/openclaw/openclaw/issues/29372) (labeled `impact:security`) asks for `subagents.workspace` to *isolate* children — i.e. their shared default is acknowledged as a security risk. Optional Docker sandboxes add `workspaceAccess: ro/rw/none` and `scope: session|agent`. |
| **picoclaw** | **One** workspace for everything (`~/.picoclaw/workspace`: sessions, memory, skills, `SOUL.md`…) | Shared; `restrict_to_workspace` applies uniformly — "no way to bypass the boundary through subagents" | Simplest possible model; fits a single-agent system, no per-subagent containment. |
| **hermes-agent** | Per *profile* `HERMES_HOME` (config, memory, sessions) | Subagent file isolation not modeled at the workspace level | Isolation is delegated to execution backends (local/Docker/SSH/Modal/Daytona) instead of directory scoping. |
| **Claude Code** | The project cwd is the workspace | Subagents share the parent's cwd | Works because runs are short-lived and repo-scoped; a long-lived personal agent accumulating state has different needs. |

Reading of the field: **single-shared is the *default* everywhere because it's easy, and
the mature projects are retrofitting isolation as a security feature** (openclaw #29372,
sandbox `workspaceAccess`). gaia already has the hard part (per-agent sandboxes with
realpath containment); what it's missing is the *orchestrator's* view over them.

## 3. The three candidate models

### A. One shared workspace (picoclaw style)
Everyone reads/writes `~/.gaia/workspace`.

- **For:** zero plumbing; souls can naturally build on each other's files; one place for
  the user to look.
- **Against:**
  - **No blast radius.** A misbehaving soul (or a prompt-injected one — souls browse the
    web) can overwrite/delete every other soul's deliverables and Gaia's own state. This
    is exactly what openclaw's security-labeled issue is about.
  - **Collisions.** Two souls both writing `index.html` / `notes.md`; screenshots from
    every browser session pile into one dir.
  - **Breaks `delegate_to_soul`'s diff.** The before/after snapshot that tells the user
    "this run produced these files" becomes noise once every agent writes to the same
    tree.
  - **Kills the future per-soul state story.** A soul's `soul.md` / scratch / caches want
    a *home*; one flat dir means prefix conventions and fragile discipline.

### B. Full isolation (today)
- **For:** maximum containment; clean per-soul diffs; per-soul state home already exists.
- **Against:** the orchestrator is blind. Gaia cannot read, verify, summarize, or relay
  any deliverable; cross-soul pipelines (soul B continues soul A's output) are impossible
  without copying through the model's context. This is a current, user-visible bug, not a
  theoretical one.

### C. Hierarchical (recommended) — Gaia sees all, souls see their own
- Souls keep model B's containment: a soul's tools resolve only inside
  `~/.gaia/agents/<key>/workspace` (+ its `/tmp` scratch). A compromised or confused
  soul still cannot touch siblings.
- Gaia's sandbox gains `~/.gaia/agents/` as an extra root: it can read any soul's
  deliverables (answer "show me the page the designer built"), verify them, pass a path
  from soul A into a delegation for soul B, and curate/clean up.
- Mirrors the real trust hierarchy: Gaia is the user's proxy and the only agent that
  takes instructions *from the user alone*; souls execute narrower tasks with
  web/tool-derived (less trusted) context, so they get the narrower file authority.
  Matches the existing privilege precedent: `delegate_to_soul` is root-only too.
- Keeps everything that already works: per-soul diffing, per-soul screenshots,
  per-soul future state, the existing `Sandbox(extra_roots=...)` mechanism (no new
  machinery — it's a one-line root computation in `sandbox_for`).

### Why C over A
Containment is the non-negotiable: souls run semi-trusted, tool-fed work. A shared tree
gives up the only structural defense for convenience that C provides anyway (Gaia can
still move/copy files between workspaces when a pipeline needs it — the *orchestrator*
mediates sharing instead of an open floor).

### Why C over B
B's purity costs the orchestrator its job. The system's contract ("the soul wrote these
files") is only useful if the agent reporting it can also open them. Every prior-art
system gives *someone* the cross-cutting view (openclaw: the parent by default; Claude
Code: everyone); gaia currently gives it to no one.

## 4. Design details for the hierarchical model

1. **Layout (unchanged, slightly formalized):**
   ```
   ~/.gaia/agents/<key>/
     workspace/        # the soul's only writable tree (tools anchor here)
     …future…          # soul.md, notes, caches — sibling of workspace/, NOT inside it
   ```
   Keep future per-soul state (`soul.md` etc.) *next to* `workspace/`, not inside it, so
   the soul's free-write area can't corrupt its own definition. Whether the registry file
   (`~/.gaia/agent_registry/<key>.md`) migrates here later is an open follow-up — don't
   couple it to this change.

2. **Root access = read-write, scoped to `~/.gaia/agents/`.** Read-only sounds safer but
   creates a worse failure mode: Gaia could see files yet not fulfill "clean that up" /
   "rename it" / "combine A and B for the next soul"; the user would then do it manually.
   Gaia is the user's proxy — if you trust it to delete its own files, the same trust
   applies a directory up. (openclaw's `workspaceAccess: ro` knob shows a config escape
   hatch is cheap to add later if wanted.)

3. **Gaia's own workspace stays separate** (`~/.gaia/agents/gaia/workspace`) and remains
   its primary root, so relative paths in root-agent tool calls keep today's meaning;
   the agents tree is an *extra* root for absolute paths returned by `delegate_to_soul`.

4. **What does NOT go in any workspace:** secrets (`.env`), config (`gaia.yaml`), logs,
   memory store — the `fs_read` deny-list and root scoping must keep excluding them.
   `~/.gaia/agents/` as the extra root (not `~/.gaia/`) preserves this; the existing
   `is_denied` list stays as a second layer.

5. **Implementation sketch (small):**
   - `sandbox_for(agents_dir, agent_name)` grows a notion of the root agent (name
     `"gaia"`, today's constant) → returns
     `Sandbox(workspace, ( /tmp/gaia/gaia, agents_dir ))`.
   - The instruction in `core/agent.py:build_root_agent` mentions: deliverable paths
     returned by `delegate_to_soul` are directly readable with `fs_read`.
   - Tests: root sandbox resolves a soul-workspace path; a soul sandbox still rejects a
     sibling's path and the agents root; deny-list still blocks `.env` everywhere.

6. **Open questions to settle when implementing:**
   - Should the *shell* tool (`exec`) for the root also get the wider tree as cwd
     candidates, or stay workspace-only? (Recommend: `workdir` may target a soul
     workspace for root only; commands still default to Gaia's own.)
   - Per-soul size quotas / cleanup policy (screenshots accumulate) — separate issue.
   - A future `workspaceAccess`-style config knob (`agents.gaia.workspace_access:
     rw|ro`) if a locked-down profile is ever needed.

## 4b. Project sub-directories (per soul run)

The hierarchy above isolates *souls* from each other; it does **not** isolate one soul's
runs from each other. The same soul reuses one workspace, so asking `frontend_developer` to
build two sites makes both write `index.html` into the same dir — the second clobbers the
first. So the workspace gains a **project** layer:

```
~/.gaia/agents/<soul>/workspace/<project>/   # one project = one unit of work
```

- A `current_project` ContextVar (`tools/fs/base.py`) carries the active project slug.
  `execute_decision` sets it around the soul's nested `Runner` and resets on exit; every
  writing tool reads it at call time via `sandbox_for` (one chokepoint — fs/exec/screenshot
  all nest automatically). The root agent leaves it unset, so its own flat workspace is
  unchanged.
- `delegate_to_soul(task, project=…)` names it: reuse a slug to continue a project (edit its
  files), pass a new one to start fresh, omit it for a unique fresh slug. The returned
  `workspace` is the project dir, so `serve` gets the right tree.
- Bonus: the before/after diff is now project-scoped, so a reused soul's old, unrelated
  deliverables stop showing up as this run's files.
- This goes beyond the prior art: openclaw/picoclaw have no per-project concept (one flat
  workspace per agent); per-project subdirs are an open, unbuilt FR there (openclaw
  [#45225](https://github.com/openclaw/openclaw/issues/45225)). Cross-soul shared projects
  (`~/.gaia/projects/<p>`, multiple souls in one project) remain a separate follow-up.

## 5. Bottom line

Your instinct is the right call and is *cheap*: gaia already built the expensive half
(per-agent containment that openclaw is still retrofitting). Granting the orchestrator a
read-write view over `~/.gaia/agents/` completes the hierarchy — souls stay sandboxed,
Gaia stops being blind, deliverables become first-class, and the per-soul directory
becomes the natural home for the future `soul.md`/state files.

**Sources:** [openclaw agent workspace](https://docs.openclaw.ai/concepts/agent-workspace) ·
[openclaw multi-agent sandbox tools](https://docs.openclaw.ai/tools/multi-agent-sandbox-tools) ·
[openclaw issue #29372 — per-agent subagents.workspace](https://github.com/openclaw/openclaw/issues/29372) ·
[picoclaw security sandbox](https://docs.picoclaw.io/docs/configuration/security-sandbox/) ·
[picoclaw workspace configuration](https://www.mintlify.com/sipeed/picoclaw/configuration/workspace) ·
[hermes-agent architecture](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture) ·
[hermes-agent README](https://github.com/NousResearch/hermes-agent)
