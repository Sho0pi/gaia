# `providers/openai` — Sign in with ChatGPT

This package lets godpy run on a **ChatGPT subscription** (Plus/Pro) instead of a paid
`OPENAI_API_KEY`. It's the same mechanism OpenAI's own **Codex CLI** uses, ported to Python +
ADK. Enable it with:

```yaml
# god.yaml
llm:
  provider: openai
  model: gpt-5.5
  openai:
    use_oauth: true        # use the subscription; false = OPENAI_API_KEY via LiteLLM
```

…then `godpy llm auth openai` once to log in.

## The trick (why this isn't the normal API)

ChatGPT-account OAuth tokens **do not work against `api.openai.com/v1`**. They're only accepted
by a separate, unofficial backend — **`https://chatgpt.com/backend-api/codex/responses`** (the
"Responses" API) — with a `chatgpt-account-id` header. So two pieces are needed:

1. **OAuth login** (`device_auth.py` + `pkce.py` + `store.py`)
   - Device-code flow against `auth.openai.com`, reusing the **public Codex client id**
     (`app_EMoam…`). It's not a secret; the auth server + Responses backend only mint
     subscription tokens for that registered client, so we reuse it (as Codex/openclaw do).
     PKCE — not a client secret — protects the flow.
   - `usercode` → user enters the code at `auth.openai.com/codex/device` → poll `deviceauth/token`
     → exchange at `oauth/token` for `access` / `refresh` / `id_token`.
   - The **account id** is parsed from the `id_token` JWT (claim `https://api.openai.com/auth`).
   - Tokens are stored at `~/.godpy/openai_chatgpt.json` (`0600`, redacted from logs) and
     auto-refreshed.

2. **Model backend** (`responses_llm.py`) — `ChatGptOAuthLlm(BaseLlm)`
   - A custom ADK model that maps an `LlmRequest` to the Responses request and streams the SSE
     back as `LlmResponse`. LiteLLM can't speak this backend, hence our own.
   - Two non-obvious gotchas that took live debugging:
     - **Tool schemas** must be JSON Schema (lowercase `object`/`string`), not genai's enum
       types — otherwise `400 invalid_function_parameters`.
     - **Reasoning replay**: gpt-5.x runs stateless here (`store:false`) and emits encrypted
       `reasoning` items that must be replayed (carried on a `thought_signature` part) before
       the call they produced, or the model **re-issues the same tool call forever**.
   - The request body/headers mirror Codex exactly (`originator`, `instructions`, `text`,
     `include`, `tool_choice`, `prompt_cache_key`, …) — the backend `400`s without them.

## Caveats

- **Unofficial + undocumented.** `chatgpt.com/backend-api` can change without notice; the wire
  shape here is reconstructed from openclaw/Codex. All backend-specific code is kept in this one
  package so breakage is contained.
- **ToS.** Programmatic use of the ChatGPT backend may conflict with OpenAI's terms — it's the
  user's own account and decision.
- Valid Codex model ids are `gpt-5.5` (default), `gpt-5.4*`, `chat-latest` — **not** `gpt-5`.
