"""web_search tool: provider injection, arg validation, result capping."""

from __future__ import annotations

import pytest

from godpy.tools.web_search import MAX_RESULTS_CAP, ddg_provider, make_web_search


class _FakeProvider:
    """Records the args the tool forwards; returns a marker string."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def __call__(self, query: str, max_results: int) -> str:
        self.calls.append((query, max_results))
        return "RESULTS"


def test_forwards_cleaned_query_and_default_count() -> None:
    provider = _FakeProvider()
    web_search = make_web_search(provider)

    assert web_search("  python adk  ") == "RESULTS"
    assert provider.calls == [("python adk", 5)]  # stripped, default max_results


def test_empty_query_raises() -> None:
    web_search = make_web_search(_FakeProvider())

    with pytest.raises(ValueError, match="query must not be empty"):
        web_search("   ")


def test_max_results_capped_and_floored() -> None:
    provider = _FakeProvider()
    web_search = make_web_search(provider)

    web_search("q", max_results=999)
    web_search("q", max_results=0)

    assert provider.calls == [("q", MAX_RESULTS_CAP), ("q", 1)]


def test_ddg_provider_formats_results(monkeypatch: pytest.MonkeyPatch) -> None:
    import ddgs

    class _FakeDDGS:
        def text(self, query: str, max_results: int) -> list[dict[str, str]]:
            return [{"title": "T", "href": "http://u", "body": "snippet"}]

    monkeypatch.setattr(ddgs, "DDGS", _FakeDDGS)

    out = ddg_provider("q", 5)

    assert "1. T" in out
    assert "http://u" in out
    assert "snippet" in out


def test_ddg_provider_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    import ddgs

    class _EmptyDDGS:
        def text(self, query: str, max_results: int) -> list[dict[str, str]]:
            return []

    monkeypatch.setattr(ddgs, "DDGS", _EmptyDDGS)

    assert ddg_provider("q", 5) == "No results found."
