"""God.mcp_toolsets attaches playwright-mcp when the browser backend resolves to mcp."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from godpy.config import Settings
from godpy.god import God

pytest.importorskip("mcp", reason="needs the optional 'mcp' dep group")


class _FakeToolset:
    """Captures the kwargs build_mcp_toolsets passes to McpToolset (no real server)."""

    def __init__(self, *, connection_params: Any, tool_filter: Any, tool_name_prefix: Any) -> None:
        self.connection_params = connection_params
        self.tool_filter = tool_filter
        self.tool_name_prefix = tool_name_prefix


def _god(tmp_path: Path, yaml: str) -> God:
    config_path = tmp_path / "god.yaml"
    config_path.write_text(yaml)
    settings = Settings(agent_registry_dir=tmp_path / "registry", config_path=config_path)
    return God(settings)


def _patch(monkeypatch: pytest.MonkeyPatch, *, bunx: bool) -> None:
    monkeypatch.setattr("google.adk.tools.mcp_tool.McpToolset", _FakeToolset)
    # resolve_browser_backend + the runtime gate both read godpy.mcp.shutil.which.
    monkeypatch.setattr("godpy.mcp.shutil.which", lambda cmd: "/usr/bin/bunx" if bunx else None)


def test_mcp_backend_attaches_playwright(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, bunx=True)
    god = _god(tmp_path, "browser:\n  backend: mcp\n")

    toolsets = god.mcp_toolsets()

    assert len(toolsets) == 1
    assert toolsets[0].connection_params.server_params.command == "bunx"


def test_native_backend_attaches_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, bunx=True)
    god = _god(tmp_path, "browser:\n  backend: native\n")

    assert god.mcp_toolsets() == []


def test_mcp_backend_falls_back_when_bunx_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, bunx=False)
    god = _god(tmp_path, "browser:\n  backend: mcp\n")

    # No bunx → resolver returns native → playwright-mcp not attached.
    assert god.mcp_toolsets() == []


def test_user_playwright_server_not_doubled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, bunx=True)
    god = _god(
        tmp_path,
        "browser:\n  backend: mcp\nmcp:\n  servers:\n    - name: playwright\n      command: bunx\n",
    )

    # The synthesized server is deduped against the user's own 'playwright' entry.
    assert len(god.mcp_toolsets()) == 1
