"""generate_image tool: saves a PNG to the workspace + flows to chat as Media; ACL group."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from gaia.tools.image import generate_images, make_generate_image
from gaia.tools.image.providers import DEFAULT_MODELS


def _ctx() -> Any:
    return SimpleNamespace(agent_name="gaia")


async def test_generate_image_saves_and_returns_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("gaia.constants.AGENTS_DIR", tmp_path / "agents")

    async def fake(provider: str, prompt: str, **kw: Any) -> list[bytes]:
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


def test_default_models_have_all_three() -> None:
    assert {"gemini", "openai", "cloudflare"} <= set(DEFAULT_MODELS)


# --- cloudflare (SDXL worker) backend -------------------------------------------------


class _FakeResp:
    def __init__(self, content: bytes) -> None:
        self.content, self.status_code, self.text = content, 200, ""


class _FakeClient:
    """Captures the POST args; mimics httpx.AsyncClient as a context manager."""

    captured: ClassVar[dict[str, Any]] = {}

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *a: Any) -> bool:
        return False

    async def post(self, url: str, *, headers: Any, json: Any) -> _FakeResp:
        _FakeClient.captured = {"url": url, "headers": headers, "json": json}
        return _FakeResp(b"\xff\xd8\xff JPEG-BYTES")


async def test_cloudflare_posts_prompt_and_config(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    monkeypatch.setenv("CLOUDFLARE_AI_TOKEN", "tok123")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    out = await generate_images(
        "cloudflare",
        "yellow dude",
        aspect_ratio="16:9",
        negative_prompt="blurry",
        options={"url": "https://w.example.dev", "num_steps": 8},
    )

    cap = _FakeClient.captured
    assert out == [b"\xff\xd8\xff JPEG-BYTES"]
    assert cap["url"] == "https://w.example.dev"
    assert cap["headers"]["Authorization"] == "Bearer tok123"
    body = cap["json"]
    assert body["prompt"] == "yellow dude" and body["negative_prompt"] == "blurry"
    assert body["num_steps"] == 8 and body["width"] == 1280 and body["height"] == 720
    assert "seed" not in body  # only set keys are forwarded


async def test_cloudflare_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLOUDFLARE_AI_TOKEN", raising=False)
    with pytest.raises(ValueError, match="GAIA_CLOUDFLARE_AI_TOKEN"):
        await generate_images("cloudflare", "x", options={"url": "https://w"})


async def test_cloudflare_requires_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUDFLARE_AI_TOKEN", "tok")
    with pytest.raises(ValueError, match="cloudflare_url"):
        await generate_images("cloudflare", "x", options={})


async def test_jpeg_bytes_saved_as_jpg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gaia.constants.AGENTS_DIR", tmp_path / "agents")

    async def fake(provider: str, prompt: str, **kw: Any) -> list[bytes]:
        return [b"\xff\xd8\xff JPEG"]

    monkeypatch.setattr("gaia.tools.image.providers.generate_images", fake)
    out = await make_generate_image("cloudflare")("x", tool_context=_ctx())
    assert out["status"] == "success" and out["path"].endswith(".jpg")


def test_registry_wires_cloudflare_options() -> None:
    from gaia.config.schema import GaiaConfig, ToolConfig
    from gaia.tools.registry import default_registry

    cfg = GaiaConfig(
        tools={
            "generate_image": ToolConfig.model_validate(
                {"provider": "cloudflare", "cloudflare_url": "https://w.dev", "num_steps": 8}
            )
        }
    )
    reg = default_registry(cfg)
    assert "generate_image" in reg.names()  # registered without error with the cloudflare options


def test_configure_adk_env_exports_cloudflare_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from gaia.config import Settings, configure_adk_env

    monkeypatch.delenv("CLOUDFLARE_AI_TOKEN", raising=False)
    configure_adk_env(Settings(cloudflare_ai_token="sekret"))
    import os

    assert os.environ.get("CLOUDFLARE_AI_TOKEN") == "sekret"


def test_generate_image_is_media() -> None:
    # The handler turns the tool result into an image reply (same path as screenshots).
    from gaia.core.screenshots import _output_media

    media = _output_media("generate_image", {"status": "success", "path": "/tmp/x.png"})
    assert len(media) == 1 and str(media[0].path) == "/tmp/x.png" and media[0].caption == "image"


def test_image_acl_group() -> None:
    from gaia.acl.groups import DEFAULT_ROLE_CAPS, GROUPS

    assert "generate_image" in GROUPS["images"]
    assert "images" in DEFAULT_ROLE_CAPS["user"]
