"""Tiny ``~/.gaia/.env`` writer — the secrets file the CLI may append to.

Secrets stay in env files, never in ``gaia.yaml`` (repo rule). The file is created with
``0600`` perms, keys are updated **in place** (one line per key, never duplicated), and
unrelated lines/comments are preserved verbatim. Shared by ``gaia connect`` and the
future ``llm``/``config`` groups (issues #100/#101).
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def get_env_var(path: Path, key: str) -> str | None:
    """The value of ``key`` in the env file, or ``None`` (missing file/key)."""
    if not path.exists():
        return None
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(.*)\s*$")
    for line in path.read_text().splitlines():
        match = pattern.match(line)
        if match:
            return match.group(1).strip().strip("'\"")
    return None


def unset_env_var(path: Path, key: str) -> None:
    """Remove ``key``'s line from the env file (no-op if absent). Used when disconnecting."""
    if not path.exists():
        return
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    kept = [line for line in path.read_text().splitlines() if not pattern.match(line)]
    path.write_text("\n".join(kept) + ("\n" if kept else ""))
    os.chmod(path, 0o600)


def load_env_file(path: Path) -> dict[str, str]:
    """All ``KEY=VALUE`` pairs in an env file (quotes stripped, comments/blanks skipped)."""
    pairs: dict[str, str] = {}
    if not path.exists():
        return pairs
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        pairs[key.strip()] = value.strip().strip("'\"")
    return pairs


def set_env_var(path: Path, key: str, value: str) -> None:
    """Set ``key=value`` in the env file: update in place or append; create ``0600``."""
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    if path.exists():
        lines = path.read_text().splitlines()
        replaced = False
        for i, line in enumerate(lines):
            if pattern.match(line):
                lines[i] = f"{key}={value}"
                replaced = True
                break  # one line per key; later duplicates would shadow confusingly
        if not replaced:
            lines.append(f"{key}={value}")
        path.write_text("\n".join(lines) + "\n")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(mode=0o600)
        path.write_text(f"{key}={value}\n")
    os.chmod(path, 0o600)  # also tightens a pre-existing file holding a new secret
