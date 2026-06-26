"""Model fetch (_models) + picker multi-select + setup model live-fetch/fallback."""

from __future__ import annotations

import pytest

from gaia.cli import _models, _select


def test_chat_only_filters_non_chat() -> None:
    ids = [
        "gpt-5.5",
        "o3-mini",
        "chatgpt-4o-latest",
        "text-embedding-3-large",
        "whisper-1",
        "tts-1",
        "dall-e-3",
        "gpt-4o-realtime-preview",
        "babbage-002",
    ]
    keep = [m for m in ids if _models._is_openai_chat(m)]
    assert keep == ["gpt-5.5", "o3-mini", "chatgpt-4o-latest"]  # chat/reasoning only


def test_available_models_oauth_is_empty() -> None:
    assert _models.available_models("openai", api_key="k", use_oauth=True) == []  # no list endpoint


def test_available_models_swallows_sdk_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_key: str) -> list[str]:
        raise RuntimeError("network down")

    monkeypatch.setattr(_models, "_openai_models", boom)
    assert (
        _models.available_models("openai", api_key="k", use_oauth=False) == []
    )  # → caller falls back


def test_resolve_multi_none_selected_takes_cursor() -> None:
    assert _select._resolve_multi(set(), 2) == [2]  # nothing ticked -> the cursor row
    assert _select._resolve_multi({0, 2}, 1) == [0, 2]  # ticked ones, sorted


def test_select_many_numbered_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import typer

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(typer, "prompt", lambda *a, **k: "1,3")
    out = _select.select_many("Pick", [("a", "A", ""), ("b", "B", ""), ("c", "C", "")])
    assert out == ["a", "c"]


def test_pick_model_uses_fetched_then_custom_option(monkeypatch: pytest.MonkeyPatch) -> None:
    from gaia.cli import setup

    monkeypatch.setattr("gaia.cli._models.available_models", lambda *a, **k: ["gpt-x", "gpt-y"])
    captured: dict[str, object] = {}

    def fake_select_one(title, options, default=None):  # type: ignore[no-untyped-def]
        captured["options"] = options
        return options[0][0]

    monkeypatch.setattr("gaia.cli._select.select_one", fake_select_one)
    model, fell_back = setup._pick_model(
        "openai", None, api_key="k", use_oauth=False, current="gpt-y"
    )
    assert model == "gpt-x" and fell_back is False
    vals = [o[0] for o in captured["options"]]  # type: ignore[union-attr]
    assert "gpt-x" in vals and "gpt-y" in vals and "__custom__" in vals


def test_pick_model_falls_back_to_curated(monkeypatch: pytest.MonkeyPatch) -> None:
    from gaia.cli import setup

    monkeypatch.setattr("gaia.cli._models.available_models", lambda *a, **k: [])  # fetch failed
    monkeypatch.setattr("gaia.cli._select.select_one", lambda *a, **k: "gpt-4o")
    model, fell_back = setup._pick_model("openai", None, api_key=None, use_oauth=True, current="")
    assert model == "gpt-4o" and fell_back is True  # curated list used


def test_pick_model_flag_skips_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    from gaia.cli import setup

    def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise AssertionError("must not fetch when --model given")

    monkeypatch.setattr("gaia.cli._models.available_models", boom)
    model, fell_back = setup._pick_model(
        "gemini", "gemini-2.5-pro", api_key="k", use_oauth=False, current=""
    )
    assert model == "gemini-2.5-pro" and fell_back is False
