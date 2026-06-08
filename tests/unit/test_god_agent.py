"""God.build_root_agent wires tools declared on the 'god' agent binding in god.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest

from godpy.config import Settings
from godpy.god import God


def _god_with_yaml(tmp_path: Path, yaml: str) -> God:
    config_path = tmp_path / "god.yaml"
    config_path.write_text(yaml)
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


def test_root_agent_attaches_configured_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    god = _god_with_yaml(tmp_path, "agents:\n  god:\n    tools:\n      - web_search\n")

    kwargs = _capture_root_kwargs(god, monkeypatch)

    assert kwargs["tools"] == god.tools.resolve(["web_search"])
    assert len(kwargs["tools"]) == 1  # type: ignore[arg-type]


def test_root_agent_no_tools_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    god = _god_with_yaml(tmp_path, "agents: {}\n")

    kwargs = _capture_root_kwargs(god, monkeypatch)

    assert kwargs["tools"] == []
