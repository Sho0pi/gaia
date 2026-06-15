"""Persisted ChatGPT OAuth credentials (access/refresh tokens + account id).

Stored at ``~/.gaia/openai_chatgpt.json`` with ``0600`` perms. Tokens are secrets — never
log them; :mod:`gaia.logs` redaction also scrubs token-shaped strings.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from pydantic import BaseModel, Field

from gaia import constants

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
        """Write the credentials to disk, owner-only (0600), created restricted.

        Open with ``O_CREAT|0o600`` so the token file is never world-readable, even for the
        brief window a ``write_text`` + later ``chmod`` would leave open. ``O_CREAT`` mode
        doesn't apply to an already-existing file, so keep a trailing ``chmod`` as
        belt-and-braces for an overwrite of a previously loose file.
        """
        target = path or credentials_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(self.model_dump_json(indent=2))
        target.chmod(0o600)


def load_credentials(path: Path | None = None) -> Credentials | None:
    """Load stored credentials, or ``None`` if the user hasn't logged in yet."""
    target = path or credentials_path()
    if not target.exists():
        return None
    return Credentials.model_validate(json.loads(target.read_text()))
