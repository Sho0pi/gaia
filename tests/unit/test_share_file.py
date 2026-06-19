"""share_file tool + the outbound-media event scan that surfaces its result as Media."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from gaia import constants
from gaia.tools.share import make_share_file


@pytest.fixture(autouse=True)
def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(constants, "AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(constants, "UPLOADS_DIR", tmp_path / "uploads")
    return tmp_path


def _ctx(agent: str = "gaia") -> SimpleNamespace:
    return SimpleNamespace(agent_name=agent)


async def test_share_file_reports_path_kind_caption(tmp_path: Path) -> None:
    f = tmp_path / "agents" / "gaia" / "workspace" / "report.pdf"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"%PDF-1.4")

    out = await make_share_file()(str(f), caption="here you go", tool_context=_ctx())

    assert out["status"] == "success"
    assert out["kind"] == "document" and out["caption"] == "here you go"
    assert out["path"].endswith("report.pdf")


async def test_share_file_infers_image_kind(tmp_path: Path) -> None:
    f = tmp_path / "agents" / "gaia" / "workspace" / "pic.png"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"\x89PNG")

    out = await make_share_file()(str(f), tool_context=_ctx())

    assert out["status"] == "success" and out["kind"] == "image"


async def test_share_file_rejects_path_outside_sandbox() -> None:
    out = await make_share_file()("/etc/passwd", tool_context=_ctx())
    assert out["status"] == "error"


async def test_share_file_errors_on_missing_file(tmp_path: Path) -> None:
    target = tmp_path / "agents" / "gaia" / "workspace" / "nope.pdf"
    out = await make_share_file()(str(target), tool_context=_ctx())
    assert out["status"] == "error" and "not a file" in out["error_message"]


def test_media_for_outputs_surfaces_share_file() -> None:
    from gaia.core.screenshots import media_for_outputs

    resp = SimpleNamespace(
        name="share_file",
        response={"status": "success", "path": "/tmp/a.mp4", "caption": "clip", "kind": "video"},
    )
    event = SimpleNamespace(get_function_responses=lambda: [resp])

    media = media_for_outputs([event])

    assert len(media) == 1
    assert media[0].kind == "video" and media[0].caption == "clip"
    assert media[0].path == Path("/tmp/a.mp4")
