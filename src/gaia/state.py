"""Git-backed versioning of gaia's learned artifacts (skills + souls) under ``~/.gaia``.

Every change to a skill or soul — by the self-improve loop or a manual CLI/chat op — is a
commit, so the history is auditable and any single change is revertable (``git revert``),
including one change among many (each is its own commit on its own file). Replaces the old
``.bak`` + ``improvements.jsonl``: the commit *is* the record (subject = the one-line entry,
body = the detail).

Safety: a single repo at ``~/.gaia`` with an **allowlist** ``.gitignore`` — only
``agent_registry/`` and ``skills/`` are tracked, so secrets (``.env``, ``users.json``, the
dbs, ``gaia.yaml``) can never be committed. Git is reused via subprocess (gated on
``shutil.which("git")``); with no git installed, commits degrade to no-ops (a warning).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from gaia import constants

logger = logging.getLogger(__name__)

#: Only these top-level entries under ~/.gaia are tracked; everything else is ignored.
_GITIGNORE = "/*\n!/.gitignore\n!/agent_registry/\n!/skills/\n"

#: Commit identity (never touches the user's global git config).
_IDENT = ["-c", "user.name=gaia", "-c", "user.email=gaia@localhost"]

#: The artifact paths staged on every commit (relative to the repo root).
_TRACKED = ("agent_registry", "skills")


@dataclass
class HistoryEntry:
    """One commit in the artifact history."""

    sha: str
    subject: str
    reverted: bool = False


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=check, capture_output=True, text=True
    )


class StateRepo:
    """The ``~/.gaia`` git repo holding skill/soul history."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root) if root is not None else constants.HOME_DIR

    @property
    def available(self) -> bool:
        """Whether git is installed (else commits are no-ops)."""
        return shutil.which("git") is not None

    def ensure_repo(self) -> bool:
        """Init the repo + allowlist .gitignore on first use. False if git is unavailable."""
        if not self.available:
            return False
        self._root.mkdir(parents=True, exist_ok=True)
        if not (self._root / ".git").is_dir():
            _git(self._root, "init", "-q")
            (self._root / ".gitignore").write_text(_GITIGNORE)
            _git(self._root, "add", ".gitignore")
            _git(self._root, *_IDENT, "commit", "-q", "-m", "chore: init gaia state repo")
        return True

    def commit(self, subject: str, body: str = "") -> str | None:
        """Stage the artifact dirs and commit; return the short sha (None if nothing changed)."""
        if not self.ensure_repo():
            return None
        for path in _TRACKED:
            if (self._root / path).exists():
                _git(self._root, "add", path)
        if not _git(self._root, "status", "--porcelain").stdout.strip():
            return None  # nothing staged — no-op
        message = f"{subject}\n\n{body}".strip() if body else subject
        _git(self._root, *_IDENT, "commit", "-q", "-m", message)
        return _git(self._root, "rev-parse", "--short", "HEAD").stdout.strip()

    def entries(self, limit: int = 30) -> list[HistoryEntry]:
        """Recent artifact commits (newest first); flags ones a later commit reverted."""
        if not (self._root / ".git").is_dir():
            return []
        log = _git(
            self._root, "log", f"-{limit}", "--format=%h\t%s", "--", *_TRACKED, check=False
        ).stdout
        reverted = self._reverted_shas()
        out: list[HistoryEntry] = []
        for line in log.splitlines():
            sha, _, subject = line.partition("\t")
            if sha:
                out.append(HistoryEntry(sha=sha, subject=subject, reverted=sha in reverted))
        return out

    def _reverted_shas(self) -> set[str]:
        """Short shas named by a 'This reverts commit <sha>' line in any commit body."""
        bodies = _git(self._root, "log", "--format=%b", check=False).stdout
        shas: set[str] = set()
        for line in bodies.splitlines():
            marker = "This reverts commit "
            if marker in line:
                full = line.split(marker, 1)[1].strip().rstrip(".")
                shas.add(full[:7])
        return shas

    def show(self, sha: str) -> str:
        """The full commit message + a stat of what it changed."""
        if not (self._root / ".git").is_dir():
            return "no history yet"
        res = _git(self._root, "show", "--stat", "--format=%H%n%n%B", sha, check=False)
        return res.stdout if res.returncode == 0 else f"unknown commit {sha!r}"

    def revert(self, sha: str) -> str:
        """``git revert`` ``sha``; on conflict (the file changed again later) abort + explain."""
        if not self.ensure_repo():
            return "git is not available — can't revert"
        res = _git(self._root, *_IDENT, "revert", "--no-edit", sha, check=False)
        if res.returncode == 0:
            return f"reverted {sha} (new commit undoing it)"
        _git(self._root, "revert", "--abort", check=False)
        return (
            f"could not revert {sha} automatically — it was changed again since. "
            "Revert the newer change first, or restore the file by hand."
        )


def commit_change(subject: str, body: str = "", *, root: Path | None = None) -> str | None:
    """Commit the current skill/soul state with ``subject``/``body``; best-effort (never raises)."""
    try:
        return StateRepo(root).commit(subject, body)
    except Exception as exc:
        logger.warning("state commit failed: %s", exc)
        return None
