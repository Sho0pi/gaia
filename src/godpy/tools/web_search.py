"""The ``web_search`` tool: query the web and return result titles, URLs, snippets.

The search backend is injected as a :class:`SearchProvider` so the tool stays
provider-neutral — :func:`ddg_provider` (DuckDuckGo, no API key) is the default, but
a Tavily/Serper/etc. provider can be dropped in at registry-build time without
touching the tool the model sees. The provider is imported lazily (heavy-deps
convention) so importing this module never pulls in the search SDK.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

#: Default and hard cap for ``max_results`` (mirrors the agenttools web_search tool).
DEFAULT_MAX_RESULTS = 5
MAX_RESULTS_CAP = 10


class SearchProvider(Protocol):
    """A web-search backend: a query + result count in, formatted text out."""

    def __call__(self, query: str, max_results: int) -> str: ...


def ddg_provider(query: str, max_results: int) -> str:
    """DuckDuckGo backend via the ``ddgs`` library. No API key required."""
    from ddgs import DDGS

    results = DDGS().text(query, max_results=max_results)
    if not results:
        return "No results found."
    lines = [
        f"{i}. {r.get('title', '')}\n   {r.get('href', '')}\n   {r.get('body', '')}"
        for i, r in enumerate(results, start=1)
    ]
    return "\n\n".join(lines)


def make_web_search(provider: SearchProvider) -> Callable[[str, int], str]:
    """Return the ADK ``web_search`` tool bound to ``provider``.

    ADK reads the returned function's name, signature and docstring to build the
    tool schema, so the closure is named ``web_search`` and documents its args.
    """

    def web_search(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> str:
        """Search the web and return matching titles, URLs and snippets.

        Use this to look up current information that is not in the conversation.

        Args:
            query: What to search for, e.g. 'latest ADK release notes'.
            max_results: How many results to return (1-10, default 5).

        Returns:
            A numbered list of results (title, URL, snippet), or a not-found message.
        """
        print("Seraching....", query, max_results)
        cleaned = query.strip()
        if not cleaned:
            raise ValueError("query must not be empty")
        capped = max(1, min(max_results, MAX_RESULTS_CAP))
        return provider(cleaned, capped)

    return web_search
