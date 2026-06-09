"""Shared foundation for the ``fs_*`` tools: the sandbox, path safety, and helpers.

Every ``fs_*`` tool confines its paths to one :class:`Sandbox`. The sandbox has two roots
per agent — the agent's workspace (``~/.godpy/agents/<agent>/workspace``) and a scoped
scratch dir (``/tmp/godpy/<agent>``) — realpath-resolved once so any path that escapes
them (via ``..``, an absolute path, or a symlink) is rejected.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

#: Per-read byte cap and result caps for the search tools.
MAX_READ_BYTES = 10_000_000
GLOB_MAX = 500
GREP_MAX = 200
SUBPROCESS_TIMEOUT = 30

#: Files whose contents are never returned by ``fs_read`` (secrets), matched by exact
#: name, by suffix, or by any ``.git`` path component.
_DENY_NAMES = frozenset({".env", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc"})
_DENY_SUFFIXES = frozenset({".pem", ".key", ".p12", ".pfx", ".keystore"})


class SandboxError(Exception):
    """Raised when a path escapes the sandbox or is otherwise refused."""


class Sandbox:
    """A set of allowed roots; resolves a user path or refuses to leave them."""

    def __init__(self, primary: Path, extra_roots: tuple[Path, ...] = ()) -> None:
        #: Relative paths anchor here (the agent's workspace).
        self.primary = self._ensure(primary)
        self.roots = (self.primary, *(self._ensure(r) for r in extra_roots))

    @staticmethod
    def _ensure(path: Path) -> Path:
        """Create ``path`` if missing and return it realpath-resolved."""
        path.mkdir(parents=True, exist_ok=True)
        return Path(os.path.realpath(path))

    def resolve(self, rel: str) -> Path:
        """Resolve ``rel`` to an absolute path inside an allowed root, or raise.

        Relative input anchors on :attr:`primary`; absolute input is taken as-is. The
        result is realpath-resolved (following symlinks in any existing prefix), so a
        symlink — or a ``..`` segment — that points outside every root is rejected.
        """
        if "\x00" in rel:
            raise SandboxError("path contains a null byte")
        candidate = Path(rel) if os.path.isabs(rel) else self.primary / rel
        resolved = Path(os.path.realpath(candidate))
        if not any(resolved == root or resolved.is_relative_to(root) for root in self.roots):
            raise SandboxError(f"path escapes the workspace: {rel}")
        return resolved


def _safe_dir(agent_name: str) -> str:
    """A filesystem-safe directory name for an agent (defensive; names are pre-lowered)."""
    cleaned = re.sub(r"[^a-z0-9_-]", "_", agent_name.lower()).strip("_")
    return cleaned or "agent"


def sandbox_for(agents_dir: Path, agent_name: str) -> Sandbox:
    """The sandbox for ``agent_name``: its workspace plus a scoped ``/tmp`` scratch dir."""
    name = _safe_dir(agent_name)
    return Sandbox(agents_dir / name / "workspace", (Path("/tmp/godpy") / name,))


def is_denied(path: Path) -> bool:
    """True if ``path`` is a secret-ish file that must not be read."""
    if ".git" in path.parts:
        return True
    return path.name in _DENY_NAMES or path.suffix.lower() in _DENY_SUFFIXES


def is_binary(sample: bytes) -> bool:
    """True if ``sample`` looks like binary / non-text content.

    A NUL byte is an unambiguous binary signal (and short blobs slip past the library's
    statistical model); ``binaryornot`` then catches the fuzzier cases (encodings, BOMs,
    high-byte ratios).
    """
    from binaryornot.helpers import is_binary_string

    return b"\x00" in sample or bool(is_binary_string(sample))


def err(message: str) -> dict[str, Any]:
    """A standard error result dict."""
    return {"status": "error", "error_message": message}


def run_search(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a search binary (``fd``/``rg``) in ``cwd``, capturing text output with a timeout."""
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT, check=False
    )
