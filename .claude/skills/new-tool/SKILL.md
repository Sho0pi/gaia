---
name: new-tool
description: Create a new gaia runtime tool (a callable the LLM invokes) the right way — ADK function-tool idiom, dict return, pluggable backend, registry wiring, tests. Use when adding any tool under src/gaia/tools/.
---

# Adding a gaia tool

A **tool** is a callable the LLM invokes (distinct from a **skill**, which is prompt
markdown). gaia follows ADK's function-tool best practices
(https://adk.dev/tools-custom/function-tools/#python). The canonical example is
`src/gaia/tools/web_search.py` — copy its shape.

## Rules (ADK idiom — non-negotiable)
- **Plain function, not a class.** ADK auto-generates the schema from the function's
  name, signature and docstring. No manual schema, no `Tool` subclass.
- **Name = behaviour.** The function name *is* the tool id the model sees. Clear over
  short (`web_search`, not `ws`). Define it once as a module `NAME` constant; the
  closure's name must match it.
- **Params:** type-hint everything. Required params have no default; optional params
  get a default (or `X | None = None`). **No `*args`/`**kwargs`** — ADK ignores them.
- **Docstring is the description — keep it lean.** ADK sends the *entire* docstring to
  the model on **every request** (verbatim; no parsing), so every char is a recurring
  token cost. The standard (issue #89, guarded by `tests/unit/test_tool_docstrings.py`):
  - One summary line merging purpose + "use this to". Add a second line only for a
    workflow/misuse note that prevents real errors (e.g. the browser ref flow).
  - `Args:` entries are **short phrases carrying only semantics the JSON schema can't**:
    enum values, formats ('e4' refs), ranges (1-10), path relativity. The schema already
    has types/defaults/required — never restate them. Self-evident args may be omitted.
  - **No `Returns:` block, ever.** The dict-return shape is a *code* convention (below),
    not docstring content — the model reads the runtime result.
  - `tool_context` stays undocumented (ADK injects it; never shown to the model).
  - Budget: ≤700 chars (~175 tokens) per tool; aim well under.
- **Return a dict, never raise to the model, never return a bare string.** Use
  `{"status": "success", ...}` or `{"status": "error", "error_message": "<human text>"}`.
  Validate inputs and return an error dict instead of raising.
- **Don't log in the tool.** Logging is centralized: `ToolLoggingPlugin`
  (`core/plugins.py`) emits one `tool_used` event for *every* tool call (ours, ADK
  built-ins, MCP) via ADK's `after_tool_callback`. A tool just returns its dict — no
  `done()` closure, no `log_event`, no `SELF_LOGGING_TOOLS`. The call's **arguments are
  logged automatically** (sanitized: sensitive key names like `*_token`/`api_key`
  filtered, values truncated); results are never logged, only `status`. Two duties:
  give secret-bearing params key names the filter catches (`*_token`, `*_key`,
  `passw*`…), and if a param's *name* can't signal sensitivity (free text that may
  carry a password or private fact), add it to the plugin's `_DROP` map.

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
**package** `src/gaia/tools/<area>/` — one file per tool + a `base.py` for shared
helpers (sandbox, path safety, a Protocol, a session manager). Copy `tools/fs/`:
- `base.py` holds the shared machinery; each tool file owns one `NAME` + one
  `make_<tool>(...)` builder.
- `__init__.py` re-exports the `make_*` builders and `NAME` constants only (lean
  exports — no logic).
- `default_registry` registers each tool individually (still gated per-id by
  `_is_enabled`), so any one of the bundle can be disabled alone in `gaia.yaml`.

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
- Register in `default_registry` (`src/gaia/tools/registry.py`), gated by `_is_enabled`
  — **tools are on by default**; config only *disables* (`enabled: false`) or tunes them.
  Do NOT add a per-agent `tools:` list in `gaia.yaml`; agents get every registered tool
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
