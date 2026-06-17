"""/skill command (list/show/install/remove/search) + skill_search filtering/fallback."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaia.commands.skill import SkillCommand
from gaia.skills import skill_search


def _ctx(tmp_path: Path, args: str) -> Any:
    skills = tmp_path / "skills"
    skills.mkdir(exist_ok=True)
    cfg = SimpleNamespace(skills_dir=skills, skill_index=[], tools={})
    gaia = SimpleNamespace(config=cfg)
    return SimpleNamespace(args=args, gaia=gaia)


def _seed(tmp_path: Path, name: str) -> None:
    folder = tmp_path / "skills" / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: A {name}.\n---\n\nbody\n")


async def test_install_then_list_then_remove(tmp_path: Path) -> None:
    cmd = SkillCommand()
    src = tmp_path / "src" / "imported"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text("---\nname: imported\ndescription: x\n---\n\nbody\n")

    out = await cmd.run(_ctx(tmp_path, f"install {src}"))
    assert "Installed: imported" in out

    listed = await cmd.run(_ctx(tmp_path, "list"))
    assert "imported" in listed

    removed = await cmd.run(_ctx(tmp_path, "remove imported"))
    assert "Removed" in removed and not (tmp_path / "skills" / "imported").exists()


async def test_show(tmp_path: Path) -> None:
    _seed(tmp_path, "caveman")
    out = await SkillCommand().run(_ctx(tmp_path, "show caveman"))
    assert "caveman" in out and "body" in out


async def test_unknown_subcommand(tmp_path: Path) -> None:
    out = await SkillCommand().run(_ctx(tmp_path, "frobnicate"))
    assert "Usage:" in out


async def test_agent_access_is_user() -> None:
    assert SkillCommand.agent_access == "user"


# --- skill_search ------------------------------------------------------------------------


class _Resp:
    def __init__(self, payload: list[dict[str, str]]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[dict[str, str]]:
        return self._payload


class _Client:
    def __init__(self, payload: list[dict[str, str]]) -> None:
        self._payload = payload

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, _url: str) -> _Resp:
        return _Resp(self._payload)


async def test_skill_search_filters_index(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = [
        {"name": "caveman", "description": "talk terse", "source": "https://x/caveman.git"},
        {"name": "lawyer", "description": "contracts", "source": "https://x/lawyer.git"},
    ]
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_k: _Client(manifest))
    hits = await skill_search("cave", index=["https://idx/index.json"])
    assert len(hits) == 1 and hits[0]["source"].endswith("caveman.git")


async def test_skill_search_web_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_k: _Client([]))  # empty index

    def provider(query: str, n: int, t: str | None) -> list[dict[str, str]]:
        return [{"title": "repo", "url": "https://github.com/x/skills", "snippet": ""}]

    hits = await skill_search(
        "anything", index=["https://idx/index.json"], search_provider=provider
    )
    assert hits and hits[0]["source"] == "https://github.com/x/skills"
