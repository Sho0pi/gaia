"""ChatGPT OAuth: PKCE, credential store, device-code login, JWT account id."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from godpy.providers.openai import device_auth
from godpy.providers.openai.pkce import generate_pkce
from godpy.providers.openai.store import Credentials, load_credentials


def test_pkce_is_valid_s256() -> None:
    p = generate_pkce()
    expected = base64.urlsafe_b64encode(hashlib.sha256(p.verifier.encode()).digest())
    assert p.challenge == expected.decode().rstrip("=")


def test_credentials_roundtrip_and_perms(tmp_path: Path) -> None:
    path = tmp_path / "creds.json"
    Credentials(
        access_token="a", refresh_token="r", account_id="acc", expires_at=time.time() + 3600
    ).save(path)

    assert oct(path.stat().st_mode & 0o777) == "0o600"
    loaded = load_credentials(path)
    assert loaded is not None and loaded.account_id == "acc"
    assert load_credentials(tmp_path / "missing.json") is None


def test_is_expired() -> None:
    fresh = Credentials(access_token="a", refresh_token="r", account_id="x", expires_at=10_000)
    assert fresh.is_expired(now=9_000) is False
    assert fresh.is_expired(now=9_999) is True  # within the skew window


def _jwt(payload: dict[str, Any]) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({})}.{enc(payload)}.sig"


def test_account_id_from_id_token() -> None:
    token = _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acc_42"}})
    assert device_auth.account_id_from_id_token(token) == "acc_42"
    assert device_auth.account_id_from_id_token("not-a-jwt") == ""


class _Resp:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self._payload, self.status_code = payload, status

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        pass


class _FakeClient:
    """Scripts responses by URL; the device-token poll is pending once, then succeeds."""

    def __init__(self) -> None:
        self.polls = 0

    async def post(self, url: str, **kwargs: Any) -> _Resp:
        if url == device_auth.USERCODE_URL:
            return _Resp({"device_auth_id": "d1", "user_code": "WXYZ", "interval": 0})
        if url == device_auth.DEVICE_TOKEN_URL:
            self.polls += 1
            if self.polls == 1:
                return _Resp({})  # authorization_pending
            return _Resp({"authorization_code": "code123", "code_verifier": "ver"})
        if url == device_auth.OAUTH_TOKEN_URL:
            return _Resp(
                {
                    "access_token": "at",
                    "refresh_token": "rt",
                    "id_token": _jwt(
                        {"https://api.openai.com/auth": {"chatgpt_account_id": "acc"}}
                    ),
                    "expires_in": 3600,
                }
            )
        raise AssertionError(url)


async def test_device_login_polls_then_exchanges() -> None:
    client = _FakeClient()
    printed: list[str] = []

    async def no_sleep(_s: float) -> None:
        return None

    creds = await device_auth.login(client=client, printer=printed.append, sleep=no_sleep)

    assert client.polls == 2  # pending, then code
    assert creds.access_token == "at" and creds.account_id == "acc"
    assert any("WXYZ" in line for line in printed)  # user code shown
