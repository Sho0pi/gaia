"""Lazy-import contract: ``godpy --help`` must not pull ADK or the app stack.

Runs in a subprocess — the pytest process itself imports godpy.app / google.adk
through other tests, so an in-process ``sys.modules`` assertion would be polluted.
Offline and fast (one interpreter start + typer import).
"""

from __future__ import annotations

import subprocess
import sys

_SNIPPET = """
import sys
from typer.testing import CliRunner
from godpy.cli import app

result = CliRunner().invoke(app, ["--help"])
assert result.exit_code == 0, result.output
for heavy in ("google.adk", "godpy.app", "godpy.god", "godpy.connectors", "textual"):
    assert heavy not in sys.modules, heavy + " imported by --help"
print("ok")
"""


def test_help_does_not_import_heavy_stack() -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _SNIPPET], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout
