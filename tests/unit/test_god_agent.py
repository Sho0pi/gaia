"""God.build_root_agent attaches every registered tool to the root agent by default."""

from __future__ import annotations

from pathlib import Path

import pytest

from godpy.config import Settings
from godpy.god import God


def _god(tmp_path: Path) -> God:
    # web_search is installed only when an engine is configured.
    config_path = tmp_path / "god.yaml"
    config_path.write_text("tools:\n  web_search:\n    engine: duckduckgo\n")
    settings = Settings(agent_registry_dir=tmp_path / "registry", config_path=config_path)
    return God(settings)


def _capture_root_kwargs(god: God, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    import google.adk.agents as adk

    captured: dict[str, object] = {}

    class _Recorder:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(adk, "LlmAgent", _Recorder)
    god.build_root_agent()
    return captured


def test_root_agent_attaches_all_registered_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    god = _god(tmp_path)

    kwargs = _capture_root_kwargs(god, monkeypatch)

    assert kwargs["tools"] == god.tools.all()
    assert {getattr(t, "__name__", t) for t in kwargs["tools"]} == {"web_search"}  # type: ignore[union-attr]
