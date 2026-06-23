"""generate_image tool: saves a PNG to the workspace + flows to chat as Media; ACL group."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gaia.tools.image import generate_images, make_generate_image
from gaia.tools.image.providers import DEFAULT_MODELS


def _ctx() -> Any:
    return SimpleNamespace(agent_name="gaia")


async def test_generate_image_saves_and_returns_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gaia.constants.AGENTS_DIR", tmp_path / "agents")

    async def fake(provider: str, prompt: str, *, aspect_ratio: str, model: str) -> list[bytes]:
        return [b"\x89PNG\r\n\x1a\nFAKE"]

    monkeypatch.setattr("gaia.tools.image.providers.generate_images", fake)
    tool = make_generate_image("gemini")
    out = await tool("a red cat", tool_context=_ctx())
    assert out["status"] == "success"
    _assert_png(out["path"])


def _assert_png(path: str) -> None:
    p = Path(path)
    assert p.is_file() and p.read_bytes().startswith(b"\x89PNG")


async def test_generate_image_empty_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gaia.constants.AGENTS_DIR", tmp_path / "agents")
    out = await make_generate_image("gemini")("  ", tool_context=_ctx())
    assert out["status"] == "error"


async def test_generate_image_provider_error_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gaia.constants.AGENTS_DIR", tmp_path / "agents")

    async def boom(*a: object, **k: object) -> list[bytes]:
        raise RuntimeError("no key")

    monkeypatch.setattr("gaia.tools.image.providers.generate_images", boom)
    out = await make_generate_image("gemini")("x", tool_context=_ctx())
    assert out["status"] == "error" and "no key" in out["error_message"]


async def test_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unknown image provider"):
        await generate_images("bogus", "x")


def test_default_models_have_both() -> None:
    assert "gemini" in DEFAULT_MODELS and "openai" in DEFAULT_MODELS


def test_generate_image_is_media() -> None:
    # The handler turns the tool result into an image reply (same path as screenshots).
    from gaia.core.screenshots import _output_media

    media = _output_media("generate_image", {"status": "success", "path": "/tmp/x.png"})
    assert len(media) == 1 and str(media[0].path) == "/tmp/x.png" and media[0].caption == "image"


def test_image_acl_group() -> None:
    from gaia.acl.groups import DEFAULT_ROLE_CAPS, GROUPS

    assert "generate_image" in GROUPS["images"]
    assert "images" in DEFAULT_ROLE_CAPS["user"]
