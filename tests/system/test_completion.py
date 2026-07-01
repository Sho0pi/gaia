"""System test: the shell-completion install → show → uninstall roundtrip, end to end.

Marked ``system`` because it drives Typer's real completion machinery and writes actual files
(into a monkeypatched HOME) rather than a fake. No external resource, no key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.cli import completion

pytestmark = pytest.mark.system


def test_install_writes_scripts_then_uninstall_removes_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # Install for bash explicitly (no shell auto-detection under CI).
    shell, path = completion.run_install(shell="bash")
    assert shell == "bash"
    assert path.exists() and path.read_text().strip()  # the completion script was written
    assert "source" in (tmp_path / ".bashrc").read_text()  # and sourced from .bashrc

    # `show` renders a script wired to gaia's completion env var.
    from typer._completion_shared import get_completion_script

    script = get_completion_script(prog_name="gaia", complete_var="_GAIA_COMPLETE", shell="bash")
    assert "_GAIA_COMPLETE" in script

    # Uninstall removes the script and strips the `.bashrc` source line.
    removed = completion.run_uninstall()
    assert path in removed and not path.exists()
    assert "gaia.sh" not in (tmp_path / ".bashrc").read_text()
