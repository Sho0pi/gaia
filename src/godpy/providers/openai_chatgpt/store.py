"""Persisted ChatGPT OAuth credentials (access/refresh tokens + account id).

Stored at ``~/.godpy/openai_chatgpt.json`` with ``0600`` perms. Tokens are secrets — never
log them; :mod:`godpy.logs` redaction also scrubs token-shaped strings.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from pydantic import BaseModel, Field

from godpy import constants

#: Refresh this many seconds before the access token actually expires.
_EXPIRY_SKEW = 60.0


def credentials_path() -> Path:
    """Where the ChatGPT OAuth credentials live."""
    return constants.HOME_DIR / "openai_chatgpt.json"


class Credentials(BaseModel):
    """ChatGPT OAuth tokens + the account id parsed from the id_token."""

    access_token: str
    refresh_token: str
    account_id: str
    id_token: str = ""
    expires_at: float = Field(default=0.0, description="Unix epoch seconds for access expiry.")

    def is_expired(self, *, now: float | None = None) -> bool:
        """True once the access token is at/near expiry (with a safety skew)."""
        return (now if now is not None else time.time()) >= self.expires_at - _EXPIRY_SKEW

    def save(self, path: Path | None = None) -> None:
        """Write the credentials to disk with owner-only (0600) permissions."""
        target = path or credentials_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.model_dump_json(indent=2))
        target.chmod(0o600)


def load_credentials(path: Path | None = None) -> Credentials | None:
    """Load stored credentials, or ``None`` if the user hasn't logged in yet."""
    target = path or credentials_path()
    if not target.exists():
        return None
    return Credentials.model_validate(json.loads(target.read_text()))
