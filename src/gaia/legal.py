"""First-run usage acceptance (issue #251).

Gaia runs LLM-driven actions on the user's machine (shell, browser, filesystem, messages sent as
them), so we require an explicit, recorded acceptance of the disclaimer before any command runs.
:func:`ensure_accepted` is the gate — wired as the CLI's persistent pre-run (the root callback).

Acceptance is recorded once in ``~/.gaia/accepted.json`` and is version-aware: bump
``ACCEPTANCE_VERSION`` when the disclaimer materially changes and everyone re-accepts. Headless runs
(no TTY) accept by setting ``GAIA_ACCEPT_TERMS=1``.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from gaia import constants

#: Bump when the disclaimer materially changes — invalidates older recorded acceptances.
ACCEPTANCE_VERSION = 1

DISCLAIMER = """\
⚠  Gaia runs LLM-driven actions on YOUR machine — shell commands, a browser, your files, and
   messages sent as you. Models get things wrong and can be tricked, so it can do dumb or
   destructive things.

   It is provided AS IS, with NO WARRANTY, and the authors are NOT LIABLE for anything it does
   (MIT License). You alone are responsible for where you run it and what it does. Keep it on
   least privilege.
"""


def accepted_path() -> Path:
    """Where the recorded acceptance lives (``~/.gaia/accepted.json``)."""
    return constants.HOME_DIR / "accepted.json"


def _gaia_version() -> str:
    import importlib.metadata

    try:
        return importlib.metadata.version("gaia")
    except importlib.metadata.PackageNotFoundError:  # uninstalled tree
        from gaia import __version__

        return __version__


def is_accepted() -> bool:
    """True if a recorded acceptance covers the current ``ACCEPTANCE_VERSION``."""
    try:
        data = json.loads(accepted_path().read_text())
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and int(data.get("version", 0)) >= ACCEPTANCE_VERSION


def record_acceptance() -> None:
    """Write the acceptance record (owner-only)."""
    path = accepted_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": ACCEPTANCE_VERSION,
                "accepted_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "gaia_version": _gaia_version(),
            }
        )
    )
    path.chmod(0o600)


def ensure_accepted() -> None:
    """Persistent pre-run gate: require an explicit, recorded acceptance before anything runs.

    Fast path when already accepted. Headless (``GAIA_ACCEPT_TERMS=1``) records and continues. On a
    TTY, the user must type ``accept``. Otherwise it refuses (``SystemExit(2)``) — gaia never runs
    on unaccepted terms.
    """
    if is_accepted():
        return
    if os.environ.get("GAIA_ACCEPT_TERMS"):
        record_acceptance()
        return
    print(DISCLAIMER, file=sys.stderr)
    if sys.stdin.isatty():
        answer = input('Type "accept" to agree (anything else cancels): ').strip().lower()
        if answer == "accept":
            record_acceptance()
            return
        print("Not accepted — exiting.", file=sys.stderr)
        raise SystemExit(2)
    print(
        "Refusing to run: terms not accepted. Run `gaia` once in a terminal to accept, or set "
        "GAIA_ACCEPT_TERMS=1 for headless use.",
        file=sys.stderr,
    )
    raise SystemExit(2)
