"""web_search tool: engine selection, arg validation, capping, dict return shape."""

from __future__ import annotations

import pytest

from gaia.tools.web_search import (
    MAX_RESULTS_CAP,
    ddg_provider,
    get_search_provider,
    make_web_search,
)


class _FakeProvider:
    """Records the args the tool forwards; returns one canned result."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str | None]] = []

    def __call__(self, query: str, max_results: int, timelimit: str | None) -> list[dict[str, str]]:
        self.calls.append((query, max_results, timelimit))
        return [{"title": "T", "url": "http://u", "snippet": "s"}]


def test_success_shape_and_forwarded_args() -> None:
    provider = _FakeProvider()
    web_search = make_web_search(provider)

    out = web_search("  python adk  ")

    assert out == {
        "status": "success",
        "results": [{"title": "T", "url": "http://u", "snippet": "s"}],
    }
    assert provider.calls == [("python adk", 5, None)]  # stripped, default count, no time limit


def test_empty_query_returns_error_dict() -> None:
    out = make_web_search(_FakeProvider())("   ")

    assert out["status"] == "error"
    assert "empty" in out["error_message"]


def test_time_range_mapped_to_timelimit() -> None:
    provider = _FakeProvider()

    make_web_search(provider)("q", time_range="Week")

    assert provider.calls[0][2] == "w"  # case-insensitive map


def test_invalid_time_range_returns_error_dict() -> None:
    out = make_web_search(_FakeProvider())("q", time_range="decade")

    assert out["status"] == "error"
    assert "time_range" in out["error_message"]


def test_max_results_capped_and_floored() -> None:
    provider = _FakeProvider()
    web_search = make_web_search(provider)

    web_search("q", max_results=999)
    web_search("q", max_results=0)

    assert [c[1] for c in provider.calls] == [MAX_RESULTS_CAP, 1]


def test_provider_exception_returns_error_dict() -> None:
    def boom(query: str, max_results: int, timelimit: str | None) -> list[dict[str, str]]:
        raise TimeoutError("network down")

    out = make_web_search(boom)("anything")

    assert out["status"] == "error"
    assert "network down" in out["error_message"]


def test_get_search_provider_by_name_and_unknown() -> None:
    assert get_search_provider("duckduckgo") is ddg_provider
    with pytest.raises(ValueError, match="unknown web_search engine 'bing'"):
        get_search_provider("bing")


def test_ddg_provider_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    import ddgs

    class _FakeDDGS:
        def text(self, query: str, max_results: int, timelimit: str | None) -> list[dict[str, str]]:
            return [{"title": "T", "href": "http://u", "body": "snippet"}]

    monkeypatch.setattr(ddgs, "DDGS", _FakeDDGS)

    out = ddg_provider("q", 5, None)

    assert out == [{"title": "T", "url": "http://u", "snippet": "snippet"}]


def _fake_settings(monkeypatch: pytest.MonkeyPatch, key: str | None) -> None:
    monkeypatch.setattr(
        "gaia.config.get_settings", lambda *a, **k: type("S", (), {"brave_api_key": key})()
    )


def test_brave_provider_maps_fields_and_sends_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from gaia.tools import web_search

    _fake_settings(monkeypatch, "tok")
    captured: dict[str, object] = {}

    def fake_get(url: str, *, params: dict, headers: dict, timeout: int) -> httpx.Response:
        captured.update(url=url, params=params, headers=headers)
        return httpx.Response(
            200,
            json={"web": {"results": [{"title": "T", "url": "https://x", "description": "snip"}]}},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    out = web_search.get_search_provider("brave")("hello", 5, "d")
    assert out == [{"title": "T", "url": "https://x", "snippet": "snip"}]
    assert captured["headers"]["X-Subscription-Token"] == "tok"  # type: ignore[index]
    assert captured["params"]["q"] == "hello" and captured["params"]["freshness"] == "pd"  # type: ignore[index]


def test_brave_without_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from gaia.tools import web_search

    _fake_settings(monkeypatch, None)
    with pytest.raises(ValueError, match="BRAVE_API_KEY"):
        web_search.get_search_provider("brave")
