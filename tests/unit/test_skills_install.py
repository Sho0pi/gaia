"""install/remove skills (local path + git) and the /skill command (list/show/install/
remove/search)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaia.commands.skill import SkillCommand
from gaia.skills import install_skill, list_skill_ids, remove_skills, skill_search


def _skill_folder(root: Path, name: str, body: str = "Do the thing.") -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A {name} skill.\n---\n\n{body}\n"
    )
    return folder


def test_install_single_local_folder(tmp_path: Path) -> None:
    src = _skill_folder(tmp_path / "src", "summarize")
    skills = tmp_path / "skills"
    assert install_skill(skills, str(src)) == ["summarize"]
    assert (skills / "summarize" / "SKILL.md").is_file()


def test_install_folder_of_skills(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _skill_folder(pack, "alpha")
    _skill_folder(pack, "beta")
    skills = tmp_path / "skills"
    assert sorted(install_skill(skills, str(pack))) == ["alpha", "beta"]


def test_install_claude_plugin_layout(tmp_path: Path) -> None:
    # A skills-pack repo nests them under .claude/skills/<name>/SKILL.md, not at the root.
    repo = tmp_path / "repo"
    (repo / "README.md").parent.mkdir(parents=True)
    (repo / "README.md").write_text("# a repo")
    _skill_folder(repo / ".claude" / "skills", "ui-ux-pro-max")
    _skill_folder(repo / ".claude" / "skills", "banner-design")
    skills = tmp_path / "skills"
    assert sorted(install_skill(skills, str(repo))) == ["banner-design", "ui-ux-pro-max"]
    assert (skills / "ui-ux-pro-max" / "SKILL.md").is_file()


async def test_install_command_refreshes_toolset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Installing via /skill must drop the cached skills toolset singleton (built once at startup),
    # else a freshly installed skill stays invisible to running agents until a restart.
    from types import SimpleNamespace

    from gaia.commands.base import CommandContext

    src = _skill_folder(tmp_path / "src", "summarize")
    skills = tmp_path / "skills"
    monkeypatch.setattr("gaia.skills.resolve_skills_dir", lambda config: skills)
    reset_calls: list[int] = []
    container = SimpleNamespace(skill_toolsets=SimpleNamespace(reset=lambda: reset_calls.append(1)))
    ctx = CommandContext(
        args=f"install {src}",
        gaia=SimpleNamespace(config=None, container=container),  # type: ignore[arg-type]
        handler=SimpleNamespace(),  # type: ignore[arg-type]
        registry=SimpleNamespace(),  # type: ignore[arg-type]
        user_id="u",
        session_id="s",
        role="user",
    )

    out = await SkillCommand().run(ctx)

    assert "Installed" in out and "/reset" in out  # honest message, not "ready right away"
    assert reset_calls == [1]  # toolset singleton was refreshed
    assert (skills / "summarize" / "SKILL.md").is_file()


def test_install_rename_single(tmp_path: Path) -> None:
    src = _skill_folder(tmp_path / "src", "summarize")
    skills = tmp_path / "skills"
    assert install_skill(skills, str(src), name="My Summary!") == ["my-summary"]


def test_install_refuses_overwrite_then_force(tmp_path: Path) -> None:
    src = _skill_folder(tmp_path / "src", "summarize")
    skills = tmp_path / "skills"
    install_skill(skills, str(src))
    with pytest.raises(FileExistsError):
        install_skill(skills, str(src))
    assert install_skill(skills, str(src), force=True) == ["summarize"]


def test_install_rejects_malformed(tmp_path: Path) -> None:
    # Folder name != frontmatter name -> ADK validation fails -> removed.
    bad = tmp_path / "src" / "mismatch"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\nname: other\ndescription: x\n---\n\nbody\n")
    skills = tmp_path / "skills"
    with pytest.raises(ValueError):
        install_skill(skills, str(bad))
    assert not (skills / "mismatch").exists()  # cleaned up


def test_install_missing_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        install_skill(tmp_path / "skills", str(tmp_path / "nope"))


def test_remove_skills_glob_and_all(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    for n in ("huashu-image", "huashu-video", "other"):
        _skill_folder(skills, n)
    # glob removes the matching family, leaves the rest
    assert sorted(remove_skills(skills, ["huashu-*"])) == ["huashu-image", "huashu-video"]
    assert list_skill_ids(skills) == ["other"]
    # 'all' clears whatever remains
    assert remove_skills(skills, ["all"]) == ["other"]
    assert list_skill_ids(skills) == []


def test_remove_skills_no_match(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    _skill_folder(skills, "keep")
    assert remove_skills(skills, ["ghost-*"]) == []
    assert list_skill_ids(skills) == ["keep"]


def test_install_from_git_file_url(tmp_path: Path) -> None:
    # A real local git repo served over file:// exercises the clone branch (no network).
    repo = tmp_path / "repo"
    _skill_folder(repo, "fromgit")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
        check=True,
    )
    skills = tmp_path / "skills"
    assert install_skill(skills, f"file://{repo}") == ["fromgit"]
    assert "fromgit" in list_skill_ids(skills)


# --- /skill command (was test_skill_command.py) ------------------------------------------


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
