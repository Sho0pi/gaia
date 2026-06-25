"""The ``web_search`` tool: query the web and return result titles, URLs, snippets.

The search backend is pluggable: each engine is a :class:`SearchProvider`, looked up
by name in :data:`SEARCH_ENGINES`. The engine is **required config** — it must be set
via ``tools.web_search.engine`` in ``gaia.yaml`` (only ``duckduckgo`` exists today);
without it the tool is not installed. The provider SDK is imported lazily (heavy-deps
convention) so importing this module never pulls in a search SDK.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from gaia.tools._helpers import err, ok

#: Tool id, used by the registry and as the ADK tool name (matches the closure name).
NAME = "web_search"

#: Default and hard cap for ``max_results``.
DEFAULT_MAX_RESULTS = 5
MAX_RESULTS_CAP = 10

#: Accepted ``time_range`` values mapped to the ddgs ``timelimit`` codes.
TIME_RANGES = {"day": "d", "week": "w", "month": "m", "year": "y"}


class SearchProvider(Protocol):
    """A web-search engine: query + count + optional recency filter, results out."""

    def __call__(
        self, query: str, max_results: int, timelimit: str | None
    ) -> list[dict[str, str]]: ...


def ddg_provider(query: str, max_results: int, timelimit: str | None) -> list[dict[str, str]]:
    """DuckDuckGo engine via the ``ddgs`` library. No API key required."""
    from ddgs import DDGS

    raw = DDGS().text(query, max_results=max_results, timelimit=timelimit)
    return [
        {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
        for r in raw
    ]


#: ddgs ``timelimit`` codes mapped to Brave's ``freshness`` codes (pd/pw/pm/py).
_BRAVE_FRESHNESS = {"d": "pd", "w": "pw", "m": "pm", "y": "py"}


def _make_brave_provider(api_key: str) -> SearchProvider:
    """Brave Search API provider — one header key, maps web results to the common shape."""

    def brave(query: str, max_results: int, timelimit: str | None) -> list[dict[str, str]]:
        import httpx

        params: dict[str, str | int] = {"q": query, "count": max_results}
        if timelimit and timelimit in _BRAVE_FRESHNESS:
            params["freshness"] = _BRAVE_FRESHNESS[timelimit]
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params=params,
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            timeout=20,
        )
        resp.raise_for_status()
        results = (resp.json().get("web") or {}).get("results") or []
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("description", ""),
            }
            for r in results
        ]

    return brave


#: A search engine factory: ``() -> SearchProvider``, self-sourcing its API key from settings.
EngineFactory = Callable[[], SearchProvider]


def _ddg_factory() -> SearchProvider:
    return ddg_provider


def _brave_factory() -> SearchProvider:
    from gaia.config import get_settings

    key = get_settings().brave_api_key
    if not key:
        raise ValueError("brave web_search needs an API key — set BRAVE_API_KEY in ~/.gaia/.env")
    return _make_brave_provider(key)


#: Registered search engines by id; the config picks one by name (no default).
SEARCH_ENGINES: dict[str, EngineFactory] = {
    "duckduckgo": _ddg_factory,
    "brave": _brave_factory,
}


def get_search_provider(engine: str) -> SearchProvider:
    """Return the :class:`SearchProvider` for ``engine``; raise if unknown or missing its key.

    There is no default engine — it must be named explicitly (from config). An API-key engine
    (e.g. brave) raises ``ValueError`` when its key is unset, so the registry can mark the tool
    missing with a clear message instead of registering a broken tool.
    """
    try:
        factory = SEARCH_ENGINES[engine.lower()]
    except KeyError:
        known = ", ".join(sorted(SEARCH_ENGINES))
        raise ValueError(f"unknown web_search engine {engine!r}; available: {known}") from None
    return factory()


def make_web_search(provider: SearchProvider) -> Callable[..., dict[str, Any]]:
    """Return the ADK web_search tool bound to ``provider``.

    ADK reads the returned function's name, signature and docstring to build the tool
    schema, so the closure's name matches :data:`NAME` and documents its args + return.
    """

    def web_search(
        query: str, max_results: int = DEFAULT_MAX_RESULTS, time_range: str | None = None
    ) -> dict[str, Any]:
        """Search the web for current information; returns titles, URLs, and snippets.

        Args:
            query: the search query.
            max_results: how many results (1-10).
            time_range: recency filter — 'day', 'week', 'month' or 'year'; empty = no limit.
        """
        cleaned = query.strip()

        if not cleaned:
            return err("query must not be empty")

        timelimit: str | None = None
        if time_range:
            timelimit = TIME_RANGES.get(time_range.strip().lower())
            if timelimit is None:
                allowed = ", ".join(TIME_RANGES)
                return err(f"time_range must be empty or one of: {allowed}")

        capped = max(1, min(max_results, MAX_RESULTS_CAP))
        # The provider hits the network (DNS, rate limits, SDK changes); a raised
        # exception would skip ()'s logging and surface to the model as a fault.
        # Match every other tool: return an error dict instead of raising.
        try:
            results = provider(cleaned, capped, timelimit)
        except Exception as exc:
            return err(f"search failed: {exc}")
        return ok(results=results)

    return web_search
