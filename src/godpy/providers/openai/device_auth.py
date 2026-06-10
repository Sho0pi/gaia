"""ChatGPT "Sign in with ChatGPT" device-code OAuth (Codex flow).

Headless-friendly: print a short code, the user approves it at auth.openai.com/codex/device,
we poll for an authorization code and exchange it for tokens. Mechanics mirror openclaw's
``extensions/openai/openai-chatgpt-device-code.ts``. ``httpx`` is imported lazily (it ships
with the optional ``llm`` dep group).
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from godpy.providers.openai.store import Credentials

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx

# The OAuth endpoints + client id are OpenAI's **Codex CLI** ones (the README in this folder
# explains the full trick). CLIENT_ID is a *public* client identifier — not a secret — and we
# reuse Codex's because the ChatGPT OAuth server + the Responses backend only grant subscription
# tokens to that registered client. PKCE (not a client secret) is what secures the device flow.
AUTH_BASE = "https://auth.openai.com"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
USERCODE_URL = f"{AUTH_BASE}/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = f"{AUTH_BASE}/api/accounts/deviceauth/token"
OAUTH_TOKEN_URL = f"{AUTH_BASE}/oauth/token"
VERIFY_URL = f"{AUTH_BASE}/codex/device"
REDIRECT_URI = f"{AUTH_BASE}/deviceauth/callback"
SCOPE = "openid profile email offline_access"

_AUTH_CLAIM = "https://api.openai.com/auth"
_TIMEOUT_S = 15 * 60
_DEFAULT_INTERVAL_S = 5.0


def account_id_from_id_token(id_token: str) -> str:
    """Pull ``chatgpt_account_id`` out of the id_token JWT payload (unverified decode)."""
    parts = id_token.split(".")
    if len(parts) < 2:
        return ""
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    payload: dict[str, Any] = json.loads(base64.urlsafe_b64decode(padded))
    auth = payload.get(_AUTH_CLAIM)
    account = auth.get("chatgpt_account_id") if isinstance(auth, dict) else None
    return account if isinstance(account, str) else ""


def _to_credentials(token: dict[str, Any]) -> Credentials:
    id_token = token.get("id_token", "")
    return Credentials(
        access_token=token["access_token"],
        refresh_token=token["refresh_token"],
        id_token=id_token,
        account_id=account_id_from_id_token(id_token),
        expires_at=time.time() + float(token.get("expires_in", 0)),
    )


async def _exchange_code(client: httpx.AsyncClient, code: str, verifier: str) -> Credentials:
    resp = await client.post(
        OAUTH_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": REDIRECT_URI,
        },
    )
    resp.raise_for_status()
    return _to_credentials(resp.json())


async def login(
    *,
    client: httpx.AsyncClient | None = None,
    printer: Callable[[str], None] = print,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> Credentials:
    """Run the device-code login and return fresh :class:`Credentials`.

    Prints the user code + verification URL via ``printer``; polls until the user approves
    (or 15 min elapses). ``client``/``sleep`` are injectable for tests.
    """
    owns_client = client is None
    if client is None:
        import httpx

        client = httpx.AsyncClient(timeout=30)
    try:
        start = await client.post(USERCODE_URL, json={"client_id": CLIENT_ID})
        start.raise_for_status()
        body = start.json()
        device_auth_id = body["device_auth_id"]
        user_code = body.get("user_code") or body.get("usercode")
        interval = float(body.get("interval") or _DEFAULT_INTERVAL_S)
        printer(f"To sign in, go to {VERIFY_URL} and enter code: {user_code}")

        deadline = time.time() + _TIMEOUT_S
        while time.time() < deadline:
            poll = await client.post(
                DEVICE_TOKEN_URL, json={"device_auth_id": device_auth_id, "user_code": user_code}
            )
            if poll.status_code == 200:
                data = poll.json()
                code, verifier = data.get("authorization_code"), data.get("code_verifier")
                if code and verifier:
                    creds = await _exchange_code(client, code, verifier)
                    printer("Signed in to ChatGPT.")
                    return creds
            await sleep(interval)
        raise TimeoutError("ChatGPT device login timed out after 15 minutes")
    finally:
        if owns_client:
            await client.aclose()


async def refresh(creds: Credentials, *, client: httpx.AsyncClient | None = None) -> Credentials:
    """Exchange the refresh token for a fresh access token; preserve the account id."""
    owns_client = client is None
    if client is None:
        import httpx

        client = httpx.AsyncClient(timeout=30)
    try:
        resp = await client.post(
            OAUTH_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": creds.refresh_token,
            },
        )
        resp.raise_for_status()
        token = resp.json()
        token.setdefault("refresh_token", creds.refresh_token)
        token.setdefault("id_token", creds.id_token)
        refreshed = _to_credentials(token)
        return refreshed.model_copy(update={"account_id": refreshed.account_id or creds.account_id})
    finally:
        if owns_client:
            await client.aclose()
