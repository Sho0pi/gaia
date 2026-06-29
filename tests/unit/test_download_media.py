"""download_media tool — driven with a fake yt-dlp (no real network/download)."""

from __future__ import annotations

import pytest

from gaia.tools import download_media as dm


class _Ctx:
    agent_name = "tester"


async def test_download_succeeds(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("gaia.constants.AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr("gaia.tools.web_fetch._resolve_ips", lambda host: ["93.184.216.34"])
    out = tmp_path / "reel.mp4"
    out.write_bytes(b"\x00\x00")
    monkeypatch.setattr(dm, "_download", lambda url, ws: (out, "My Reel"))

    res = await dm.make_download_media()("https://example.com/reel", tool_context=_Ctx())

    assert res["status"] == "success"
    assert res["path"] == str(out) and res["title"] == "My Reel"


async def test_download_blocks_ssrf(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("gaia.constants.AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr("gaia.tools.web_fetch._resolve_ips", lambda host: ["127.0.0.1"])
    called: list[int] = []
    monkeypatch.setattr(dm, "_download", lambda url, ws: called.append(1))

    res = await dm.make_download_media()("http://localhost/x", tool_context=_Ctx())

    assert res["status"] == "error" and not called  # guarded before any download


async def test_download_failure_is_error(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("gaia.constants.AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr("gaia.tools.web_fetch._resolve_ips", lambda host: ["93.184.216.34"])

    def boom(url: str, ws: object) -> object:
        raise RuntimeError("yt-dlp blew up")

    monkeypatch.setattr(dm, "_download", boom)

    res = await dm.make_download_media()("https://example.com/x", tool_context=_Ctx())

    assert res["status"] == "error" and "blew up" in res["error_message"]
