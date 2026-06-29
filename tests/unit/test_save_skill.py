"""save_skill tool — writes a reusable SKILL via gaia.skills.write_skill."""

from __future__ import annotations

from pathlib import Path

from gaia.tools.save_skill import make_save_skill


class _Ctx:
    agent_name = "gaia"


async def test_save_skill_writes_loadable_skill(tmp_path: Path) -> None:
    res = await make_save_skill(tmp_path)(
        "Download Video",
        "Download a video from a link the user shares.",
        "Call download_media(url); the file is delivered automatically.",
        tool_context=_Ctx(),
    )

    assert res["status"] == "success" and res["skill_id"] == "download-video"
    assert (tmp_path / "download-video" / "SKILL.md").is_file()


async def test_save_skill_rejects_duplicate(tmp_path: Path) -> None:
    tool = make_save_skill(tmp_path)
    await tool("dl", "x", "do it", tool_context=_Ctx())

    res = await tool("dl", "x", "do it again", tool_context=_Ctx())

    assert res["status"] == "error" and "exists" in res["error_message"]


async def test_save_skill_requires_name_and_instructions(tmp_path: Path) -> None:
    res = await make_save_skill(tmp_path)("", "desc", "", tool_context=_Ctx())
    assert res["status"] == "error"
