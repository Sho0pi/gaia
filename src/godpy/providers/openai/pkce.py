"""PKCE helpers for the ChatGPT OAuth device flow (RFC 7636, S256)."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass


def _b64url(raw: bytes) -> str:
    """Base64url without padding (the OAuth/JWT convention)."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@dataclass(frozen=True)
class Pkce:
    """A PKCE pair: the secret ``verifier`` and its ``challenge`` (S256)."""

    verifier: str
    challenge: str


def generate_pkce() -> Pkce:
    """Return a fresh PKCE verifier + S256 challenge."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return Pkce(verifier=verifier, challenge=challenge)


def random_state() -> str:
    """An opaque anti-CSRF ``state`` value."""
    return _b64url(secrets.token_bytes(16))
