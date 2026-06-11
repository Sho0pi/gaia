---
name: new-connector
description: Add a chat connector (new messaging platform) — Handler/Send contract, lazy SDK imports, lifecycle (run vs start), config wiring, message limits, tests. Use when touching src/gaia/connectors/.
---

# Adding a connector

A **connector is a dumb pipe**: it hands inbound text to the shared `Handler`
coroutine with a `Send` callback and pushes each reply back through `Send`.
No business logic, no gaia imports beyond `connectors/base.py` and `constants`.
Canonical examples: `connectors/telegram.py` (async lifecycle done by hand),
`connectors/whatsapp_web.py` (native lib + event callbacks).

## Shape
```python
class FooConnector:
    def __init__(self, <creds>, handler: Handler) -> None: ...
    def build_client(self) -> Any:     # wire SDK ↔ handler; SDK imported HERE (lazily)
    def run(self) -> None:             # standalone: owns the event loop, blocks
    async def start(self) -> None:     # co-run: lives in the CALLER's loop, gather-able
```
- The platform SDK is imported **inside** `build_client`, never at module top —
  the module must import without the dep installed.
- `build_client` returns the wired client so unit tests can exercise the wiring
  with a fake SDK and system tests can build the real one offline.
- Inbound callback: extract plain text, define `async def send(reply: str)` that
  posts back to the same chat, then `await self._handler(text, send)`.
- Slash commands must flow through to the handler (it dispatches them itself) —
  don't let the SDK swallow `/...` messages (see the `filters.TEXT` comment in
  telegram.py).
- **Respect the platform's message size limit**: chunk replies in `send` (see the
  chunking helper in `connectors/base.py` once #56 lands; Telegram caps at 4096).

## Wire it
- Export from `connectors/__init__.py`.
- Add a `<Foo>ConnectorConfig` block (with `enabled: bool = False` + descriptions)
  to `ConnectorsConfig` in `config/schema.py` — the gaia.yaml scaffold regenerates
  from the schema; don't hand-edit a default file.
- Add the launch branch in `app.py`: `plan_launch` (policy, pure, unit-testable)
  and `_run_background` (task creation). Credentials come from `Settings` (env),
  never from gaia.yaml.

## Test
- **Unit** (`tests/unit/`): fake SDK module/objects; assert inbound text reaches a
  recorded handler, replies route to the right chat, and lifecycle calls happen in
  order (see test_connector_launch.py / the telegram tests).
- **System** (`tests/system/`): `pytest.importorskip` the real SDK; build a real
  client offline. Anything needing live credentials/pairing is additionally gated
  behind an explicit env var (see test_whatsapp_web.py's `GAIA_WHATSAPP_RUN_LIVE`).
