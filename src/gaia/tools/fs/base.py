"""Shared foundation for the ``fs_*`` tools: the sandbox, path safety, and helpers.

Every ``fs_*`` tool confines its paths to one :class:`Sandbox`. The sandbox has two roots
per agent — the agent's workspace (``~/.gaia/agents/<agent>/workspace``) and a scoped
scratch dir (``/tmp/gaia/<agent>``) — realpath-resolved once so any path that escapes
them (via ``..``, an absolute path, or a symlink) is rejected. The root orchestrator
alone also gets the whole agents tree as a root (hierarchical access — see
``docs/workspace-design.md``), so it can open the deliverables its souls produce while
each soul stays sealed inside its own workspace.
"""

from __future__ import annotations

import os
import re
import subprocess
from contextvars import ContextVar
from pathlib import Path

from gaia import constants
from gaia.tools._helpers import err as err  # re-export for gaia.tools.fs.* importers

#: The project a soul run is scoped to, as a slug. When set, every agent workspace nests
#: under ``workspace/<project>`` so separate projects (e.g. two different websites the same
#: soul builds) don't overwrite each other. ``execute_decision`` sets it around the soul's
#: nested Runner; the fs/exec/screenshot tools read it at call time (they all go through
#: :func:`sandbox_for`). Empty outside a soul run — the root agent stays at its flat workspace.
current_project: ContextVar[str] = ContextVar("current_project", default="")

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
    """A set of allowed roots; resolves a user path or refuses to leave them.

    Roots split two ways: ``write_roots`` (the agent's own workspace + scratch — writable)
    and read-only roots (e.g. the root orchestrator's view of the whole agents tree, which it
    may read to relay a soul's deliverables but must not edit). :meth:`resolve` checks all
    roots for a read and only ``write_roots`` for a write.
    """

    def __init__(
        self,
        primary: Path,
        extra_roots: tuple[Path, ...] = (),
        read_only_roots: tuple[Path, ...] = (),
    ) -> None:
        #: Relative paths anchor here (the agent's workspace).
        self.primary = self._ensure(primary)
        #: Roots a write may target: the workspace and its scratch dirs.
        self.write_roots = (self.primary, *(self._ensure(r) for r in extra_roots))
        #: Every root a read may target: writable ones plus the read-only ones.
        self.roots = (*self.write_roots, *(self._ensure(r) for r in read_only_roots))

    @staticmethod
    def _ensure(path: Path) -> Path:
        """Create ``path`` if missing and return it realpath-resolved."""
        path.mkdir(parents=True, exist_ok=True)
        return Path(os.path.realpath(path))

    def resolve(self, rel: str, *, write: bool = False) -> Path:
        """Resolve ``rel`` to an absolute path inside an allowed root, or raise.

        Relative input anchors on :attr:`primary`; absolute input is taken as-is. The
        result is realpath-resolved (following symlinks in any existing prefix), so a
        symlink — or a ``..`` segment — that points outside every root is rejected. A
        ``write`` is confined to :attr:`write_roots`; a read may reach any root. A path that
        is readable but not writable (another agent's workspace, for the root orchestrator)
        gets a message steering the model to re-delegate instead of editing it.
        """
        if "\x00" in rel:
            raise SandboxError("path contains a null byte")
        candidate = Path(rel) if os.path.isabs(rel) else self.primary / rel
        resolved = Path(os.path.realpath(candidate))
        allowed = self.write_roots if write else self.roots
        if any(resolved == root or resolved.is_relative_to(root) for root in allowed):
            return resolved
        if write and any(resolved == r or resolved.is_relative_to(r) for r in self.roots):
            raise SandboxError(
                f"that path is another agent's workspace (read-only here): {rel} — to change "
                "it, re-delegate to the soul with delegate_to_soul using the same project."
            )
        raise SandboxError(f"path escapes the workspace: {rel}")


def _safe_dir(agent_name: str) -> str:
    """A filesystem-safe directory name for an agent (defensive; names are pre-lowered)."""
    cleaned = re.sub(r"[^a-z0-9_-]", "_", agent_name.lower()).strip("_")
    return cleaned or "agent"


def sandbox_for(agents_dir: Path, agent_name: str) -> Sandbox:
    """The sandbox for ``agent_name``: its workspace plus a scoped ``/tmp`` scratch dir.

    Hierarchical access (issue #121, design in ``docs/workspace-design.md``): the **root
    orchestrator** additionally gets the whole agents tree as a **read-only** root, so it can
    read/verify/relay the deliverables ``delegate_to_soul`` reports (each soul's own
    workspace) but never edit them — changing a soul's project is the soul's job, reached by
    re-delegating. Souls stay confined to their own workspace, a confused or prompt-injected
    soul can never touch a sibling's files.
    """
    name = _safe_dir(agent_name)
    # Every agent also gets the shared uploads dir as a writable root, so a tool/soul can read
    # or copy a file the user sent in (e.g. an inbound image to embed in a website).
    extra: tuple[Path, ...] = (Path("/tmp/gaia") / name, constants.UPLOADS_DIR)
    read_only: tuple[Path, ...] = ()
    if name == constants.APP_NAME:  # the root agent ("gaia") reads the whole agents tree
        read_only = (agents_dir,)
    # A soul run nests its workspace under the current project, so two projects the same soul
    # builds (e.g. two websites) stay separate. Unset (root agent / no run) -> flat workspace.
    primary = agents_dir / name / "workspace"
    project = current_project.get()
    if project:
        primary = primary / _safe_dir(project)
    return Sandbox(primary, extra_roots=extra, read_only_roots=read_only)


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


def run_search(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a search binary (``fd``/``rg``) in ``cwd``, capturing text output with a timeout."""
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT, check=False
    )
