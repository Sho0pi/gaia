---
name: new-tool
description: Create a new godpy runtime tool (a callable the LLM invokes) the right way — ADK function-tool idiom, dict return, pluggable backend, registry wiring, tests. Use when adding any tool under src/godpy/tools/.
---

# Adding a godpy tool

A **tool** is a callable the LLM invokes (distinct from a **skill**, which is prompt
markdown). godpy follows ADK's function-tool best practices
(https://adk.dev/tools-custom/function-tools/#python). The canonical example is
`src/godpy/tools/web_search.py` — copy its shape.

## Rules (ADK idiom — non-negotiable)
- **Plain function, not a class.** ADK auto-generates the schema from the function's
  name, signature and docstring. No manual schema, no `Tool` subclass.
- **Name = behaviour.** The function name *is* the tool id the model sees. Clear over
  short (`web_search`, not `ws`). Define it once as a module `NAME` constant; the
  closure's name must match it.
- **Params:** type-hint everything. Required params have no default; optional params
  get a default (or `X | None = None`). **No `*args`/`**kwargs`** — ADK ignores them.
- **Docstring is the description.** One-line purpose, then `Args:` (each param) and
  `Returns:`. The model reads this to decide when/how to call.
- **Return a dict, never raise to the model, never return a bare string.** Use
  `{"status": "success", ...}` or `{"status": "error", "error_message": "<human text>"}`.
  Validate inputs and return an error dict instead of raising.
- **Log every call.** A tool call is user activity, so emit one structured event per
  invocation with `log_event("tool_used", tool=NAME, status=result["status"], …)` from
  `godpy.logs` (see `web_search.py` / `web_fetch.py`). Funnel every `return` through a
  small `done(result)` closure so success *and* every error path log exactly once. Add a
  couple of cheap context fields (e.g. `query`, `url`, result count) — **never secrets**
  (redaction is best-effort).

## Pluggable backend (when the tool wraps an external service)
- Define a `SearchProvider`-style `Protocol` for the backend and a
  `make_<tool>(provider)` closure that returns the ADK function. This keeps the tool
  backend-neutral and unit-testable with a fake provider (no network).
- Register backends in a `{name: provider}` map; pick one from **tool-specific config**
  `tools.<id>.<key>` read via `ToolConfig.model_extra` (e.g. `tools.web_search.engine`).
  Unknown value → raise at startup (fail loud).
- Import heavy SDKs **lazily inside the provider** so importing the module needs no SDK.

## Tool bundle (several related tools sharing helpers)
When a capability is several tools, not one (e.g. the `fs_*` family), make a
**package** `src/godpy/tools/<area>/` — one file per tool + a `base.py` for shared
helpers (sandbox, path safety, a Protocol, a session manager). Copy `tools/fs/`:
- `base.py` holds the shared machinery; each tool file owns one `NAME` + one
  `make_<tool>(...)` builder.
- `__init__.py` re-exports the `make_*` builders and `NAME` constants only (lean
  exports — no logic).
- `default_registry` registers each tool individually (still gated per-id by
  `_is_enabled`), so any one of the bundle can be disabled alone in `god.yaml`.

## Stateful tools (a session that persists across calls)
Some tools must keep live state between invocations (an open browser, a running
shell). Don't put a global mutable singleton in the closure:
- Keep a **per-agent** registry keyed by `tool_context.agent_name`
  (`_SESSIONS: dict[str, Session]`) in `base.py`; create lazily on first use, reuse
  after. Per-agent so one soul's session never bleeds into another's.
- **Always release it.** Add an idle timeout (lazy check on access is fine — no
  background thread needed) *and* an `atexit`/shutdown hook that closes every session,
  so nothing orphans (a stray browser/process). A system test must assert no orphan
  survives.
- Such tools are usually `async def` (the SDK ops are coroutines); ADK supports async
  tools — see `remember` / `delegate_to_soul`.

## Wire it
- Register in `default_registry` (`src/godpy/tools/registry.py`), gated by `_is_enabled`
  — **tools are on by default**; config only *disables* (`enabled: false`) or tunes them.
  Do NOT add a per-agent `tools:` list in `god.yaml`; agents get every registered tool
  (`registry.all()`), and `AgentSpec.tools` is only an optional pin.
- The registry `Tool` type is ADK's own union `Callable | BaseTool | BaseToolset`
  (imported under `TYPE_CHECKING`), so resolved lists drop into `LlmAgent(tools=...)`.
- Add any new dependency to a dependency group in `pyproject.toml` and the mypy
  `ignore_missing_imports` override if it ships no stubs.
- If the tool needs an external binary or optional dep, gate registration on its
  presence (`shutil.which("rg")`, or a `try`-import) — skip it with a startup warning
  when absent, like `fs_grep` (needs `rg`) and `fs_glob` (needs `fd`).

## Test (both tiers — green or it isn't done)
- **Unit** (`tests/unit/`): drive the tool with a fake provider. Cover the success dict
  shape, the error dict for bad input, arg capping/validation, and backend selection.
  Monkeypatch the SDK to assert field mapping without network.
- **System** (`tests/system/`, key-gated like `test_tools.py`): a subagent gets the tool
  and the real `LlmAgent` builds.

## Self-review
After: confirm `uv run ruff check`, `uv run mypy src`, `uv run pytest` are green, then
critique the diff in the PR (what's weak, what's next) per `feature-workflow`.
