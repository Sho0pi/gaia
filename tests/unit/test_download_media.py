"""download_media tool — driven with a fake yt-dlp + fake downloader fallback (no real network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.tools import download_media as dm


class _Ctx:
    agent_name = "tester"


def _public(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("gaia.constants.AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr("gaia.tools.web_fetch._resolve_ips", lambda host: ["93.184.216.34"])


async def test_yt_dlp_success_skips_the_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _public(monkeypatch, tmp_path)
    out = tmp_path / "reel.mp4"
    out.write_bytes(b"\x00\x00")
    monkeypatch.setattr(dm, "_download", lambda url, ws: (out, "My Reel"))
    fallback: list[int] = []
    monkeypatch.setattr(dm, "_resolve_via_downloader", lambda *a: fallback.append(1))

    res = await dm.make_download_media()("https://example.com/reel", tool_context=_Ctx())

    assert res["status"] == "success" and res["title"] == "My Reel"
    assert not fallback  # yt-dlp handled it; the downloader site was not touched


async def test_falls_back_to_downloader_when_yt_dlp_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _public(monkeypatch, tmp_path)

    def boom(url: str, ws: object) -> object:
        raise RuntimeError("Instagram sent an empty media response")

    monkeypatch.setattr(dm, "_download", boom)
    resolved = tmp_path / "video-1.mp4"
    resolved.write_bytes(b"x" * 2000)

    async def fake_resolver(url: str, ws: Path, cfg: object) -> Path:
        return resolved

    monkeypatch.setattr(dm, "_resolve_via_downloader", fake_resolver)

    res = await dm.make_download_media(browser_cfg=object())(
        "https://www.instagram.com/reel/X/", tool_context=_Ctx()
    )

    assert res["status"] == "success" and res["path"] == str(resolved)


async def test_error_when_both_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _public(monkeypatch, tmp_path)
    monkeypatch.setattr(dm, "_download", lambda u, w: (_ for _ in ()).throw(RuntimeError("ytfail")))

    async def fail(url: str, ws: Path, cfg: object) -> Path:
        raise RuntimeError("sitefail")

    monkeypatch.setattr(dm, "_resolve_via_downloader", fail)

    res = await dm.make_download_media()("https://www.instagram.com/reel/X/", tool_context=_Ctx())

    assert res["status"] == "error"
    assert "ytfail" in res["error_message"] and "sitefail" in res["error_message"]


async def test_blocks_ssrf_before_anything(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("gaia.constants.AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr("gaia.tools.web_fetch._resolve_ips", lambda host: ["127.0.0.1"])
    touched: list[int] = []
    monkeypatch.setattr(dm, "_download", lambda u, w: touched.append(1))
    monkeypatch.setattr(dm, "_resolve_via_downloader", lambda *a: touched.append(2))

    res = await dm.make_download_media()("http://localhost/x", tool_context=_Ctx())

    assert res["status"] == "error" and not touched  # guarded before any work
