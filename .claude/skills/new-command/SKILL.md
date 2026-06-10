---
name: new-command
description: Add an in-chat slash command (/foo) the right way — Command subclass, registry wiring, config gating, tests. Use when adding or editing anything under src/godpy/commands/.
---

# Adding a slash command

A **command** is a control-surface coroutine that runs *instead of* the LLM when a
message starts with `/`. It never reaches the model or the memory ingest path.
Canonical examples: `commands/status.py` (read-only), `commands/forget.py`
(destructive, confirm-gated).

## Pattern (one class per file — non-negotiable)
- New file `src/godpy/commands/<name>.py` with one `Command` subclass:
  ```python
  class FooCommand(Command):
      name = "foo"                  # the /name users type (lowercase)
      summary = "One line for /help."
      aliases = ("f",)              # optional
      usage = "<arg>"               # optional, shown in /help

      async def run(self, ctx: CommandContext) -> str:
          ...
  ```
- `ctx` gives you: `args` (raw string after the name), `god` (live God), `handler`
  (the conversation's GodHandler), `registry`, `user_id`, `session_id`.
- Return the reply text; the handler sends it. Plain text — connectors may not
  render markdown.
- Heavy imports (ADK types, google.genai) go **inside** `run` (lazy-dep convention).

## Rules
- **Destructive actions are confirm-gated.** Copy `/forget`: first call reports
  what would happen and demands `'/cmd yes'`; only the confirm token executes.
- **Memory-dependent commands** check `ctx.god.memory_service is None` and reply
  that memory is off instead of failing.
- Don't `log_event` yourself — the handler already logs one `command_used` event
  per dispatch.

## Wire it
- Add the instance to `_BUILTINS` in `src/godpy/commands/registry.py`.
- It is automatically on by default and gateable via `commands.<name>.enabled: false`
  in god.yaml — no schema change needed (`CommandConfig` covers it).

## Test (tests/unit/test_commands.py — follow the existing FakeGod/ctx style)
- Happy path reply content.
- Args validation (empty/garbage args → usage hint).
- Memory-off path if applicable.
- Confirm-gate paths if destructive (no-confirm → warning; confirm → action).
- `/help` includes the new line (`help_line()` output).
