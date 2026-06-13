"""browser_navigate's file:// gate: allow workspace deliverables, reject everything else."""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.tools.browser.navigate import _local_workspace_file


@pytest.fixture(autouse=True)
def agents_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("gaia.constants.AGENTS_DIR", tmp_path / "agents")
    return tmp_path / "agents"


def test_allows_html_under_agents_dir(agents_dir: Path) -> None:
    site = agents_dir / "frontend_designer" / "workspace" / "index.html"
    site.parent.mkdir(parents=True)
    site.write_text("<h1>hi</h1>")

    assert _local_workspace_file(site.as_uri()) is None


def test_rejects_file_outside_agents_dir(agents_dir: Path) -> None:
    err = _local_workspace_file("file:///etc/passwd")
    assert err is not None and "agents workspace" in err


def test_rejects_traversal_escape(agents_dir: Path) -> None:
    # A path that resolves out of AGENTS_DIR via .. is refused.
    escape = f"file://{agents_dir}/../../../../etc/hosts"
    assert _local_workspace_file(escape) is not None


def test_rejects_missing_file(agents_dir: Path) -> None:
    agents_dir.mkdir(parents=True)
    err = _local_workspace_file((agents_dir / "nope.html").as_uri())
    assert err is not None and "no such file" in err


def test_rejects_non_file_scheme(agents_dir: Path) -> None:
    assert _local_workspace_file("https://example.com") is not None
