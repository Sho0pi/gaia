---
name: new-command
description: Add an in-chat slash command (/foo) the right way — catalog entry, Command subclass, registry wiring, config gating, tests. Use when adding or editing anything under src/gaia/commands/.
---

# Adding a slash command

A **command** is a control-surface coroutine that runs *instead of* the LLM when a
message starts with `/`. It never reaches the model or the memory ingest path.
Canonical examples: `commands/status.py` (read-only), `commands/forget.py`
(destructive, confirm-gated).

A command's **description lives in `commands/catalog.py`, its behaviour in
`commands/<name>.py`** — one source, shared with the CLI. Never put summary text on
the class.

## 1. Describe it — `src/gaia/commands/catalog.py`
Add a `CommandInfo` to `CATALOG`:
```python
"foo": CommandInfo(
    "foo", "One line for /help.", "Chat & memory",   # name, summary, category
    usage="<arg>",                                   # optional, shown in /help
    details="Fuller explanation for /help foo.",      # optional
    examples=("/foo bar",),                           # optional
),
```
`category` must be one of `CATEGORY_ORDER` (a regression test enforces every command has
an entry, and that its category is known). `details`/`examples` show only in `/help <cmd>`.

## 2. Implement it — `src/gaia/commands/<name>.py` (one class per file — non-negotiable)
```python
class FooCommand(Command):
    name = "foo"                  # the /name users type (lowercase)
    aliases = ("f",)              # optional
    capability = "manage_users"   # optional ACL gate (see below); omit for open-to-all

    async def run(self, ctx: CommandContext) -> str:
        ...
```
- No `summary`/`usage` on the class — they're read from the catalog by `name`.
- `ctx` gives: `args` (raw string after the name), `gaia` (live Gaia), `handler`, `registry`,
  `user_id`, `session_id`.
- Return the reply text. WhatsApp renders `*bold*` / `_italic_` / `` `mono` ``; Telegram shows
  the raw markers — keep it plain-safe.
- Heavy imports (ADK types, google.genai) go **inside** `run` (lazy-dep convention).

## Rules
- **`capability` gates BOTH running and visibility.** A command the caller can't run is
  hidden from their `/help` (filtered via `authorize`). `"manage_users"` → admin-only.
- **Destructive actions are confirm-gated.** Copy `/forget`: first call reports what would
  happen and demands `'/cmd yes'`; only the confirm token executes.
- **Memory-dependent commands** check `ctx.gaia.memory_service is None` and reply that memory
  is off instead of failing.
- Don't `log_event` yourself — the handler logs one `command_used` event per dispatch.

## Wire it
- Add the instance to `_BUILTINS` in `src/gaia/commands/registry.py`.
- On by default, gateable via `commands.<name>.enabled: false` in gaia.yaml — no schema change.

## Share with the CLI (only if it's a true dup)
If a `gaia <name>` CLI command says the **same** thing, set its Typer `help=summary_of("<name>")`
from the catalog instead of a hand-written docstring (see `cli/acl.py`). Most CLI commands are
multi-action groups (`gaia skill list/install/…`) whose wording legitimately differs from the
single chat command — don't force-share those.

## Test (tests/unit/ — follow the existing FakeGaia/ctx style)
- `test_commands.py`: happy-path reply, args validation, memory-off path, confirm-gate paths.
- `test_command_catalog.py` already auto-checks every command has a catalog entry and that
  `/help` stays grouped + role-filtered — your new entry rides those.
