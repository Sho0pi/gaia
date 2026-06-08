"""web_fetch tool: SSRF guard, redirect revalidation, arg validation, dict return shape."""

from __future__ import annotations

import pytest

from godpy.tools import web_fetch as wf
from godpy.tools.web_fetch import (
    MAX_BYTES_CAP,
    BlockedURLError,
    make_web_fetch,
    validate_url,
)


def _resolve_to(ip: str) -> callable:  # type: ignore[valid-type]
    """A fake resolver that always returns ``ip`` regardless of host."""

    def resolver(host: str) -> list[str]:
        return [ip]

    return resolver


class _FakeFetcher:
    """Records forwarded args; returns canned HTML and a final URL."""

    def __init__(self, html: str) -> None:
        self.html = html
        self.calls: list[tuple[str, int]] = []

    def __call__(self, url: str, max_bytes: int) -> dict[str, str]:
        self.calls.append((url, max_bytes))
        return {"final_url": url, "html": self.html}


_ARTICLE = """
<html><head><title>A Title</title></head><body>
<nav>home about contact</nav>
<article><h1>Heading</h1>
<p>The quick brown fox jumps over the lazy dog. This is the readable body of the
article and it is long enough that trafilatura keeps it as the main content.</p>
<p>A second paragraph adds more substance so extraction is confident.</p></article>
<footer>copyright</footer></body></html>
"""


# --- SSRF guard --------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",  # loopback
        "::1",  # loopback v6
        "10.0.0.5",  # private
        "192.168.1.1",  # private
        "172.16.0.1",  # private
        "169.254.169.254",  # link-local / cloud metadata
        "100.64.0.1",  # CGNAT
        "224.0.0.1",  # multicast
    ],
)
def test_validate_url_blocks_internal_ips(monkeypatch: pytest.MonkeyPatch, ip: str) -> None:
    monkeypatch.setattr(wf, "_resolve_ips", _resolve_to(ip))

    error = validate_url("http://evil.test/path")

    assert error is not None
    assert "blocked address" in error


def test_validate_url_allows_public_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wf, "_resolve_ips", _resolve_to("93.184.216.34"))

    assert validate_url("https://example.com") is None


@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/passwd"])
def test_validate_url_rejects_non_http_scheme(url: str) -> None:
    error = validate_url(url)

    assert error is not None
    assert "scheme" in error


def test_validate_url_rejects_embedded_credentials() -> None:
    error = validate_url("http://user:pass@example.com/")

    assert error is not None
    assert "credentials" in error


# --- tool closure ------------------------------------------------------------------


def test_success_shape_and_extracted_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wf, "_resolve_ips", _resolve_to("93.184.216.34"))
    fetcher = _FakeFetcher(_ARTICLE)

    out = make_web_fetch(fetcher)("  https://example.com/post  ")

    assert out["status"] == "success"
    assert out["url"] == "https://example.com/post"  # stripped, passed through
    assert "quick brown fox" in out["markdown"]
    assert "home about contact" not in out["markdown"]  # nav stripped
    assert fetcher.calls == [("https://example.com/post", wf.DEFAULT_MAX_BYTES)]


def test_tool_call_is_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wf, "_resolve_ips", _resolve_to("93.184.216.34"))
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(wf, "log_event", lambda action, **f: events.append((action, f)))

    make_web_fetch(_FakeFetcher(_ARTICLE))("https://example.com/post")

    assert events[0][0] == "tool_used"
    assert events[0][1]["tool"] == "web_fetch"
    assert events[0][1]["status"] == "success"


def test_error_path_is_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(wf, "log_event", lambda action, **f: events.append((action, f)))

    make_web_fetch(_FakeFetcher(_ARTICLE))("   ")  # empty url, never fetched

    assert events == [
        ("tool_used", {"tool": "web_fetch", "url": "", "status": "error", "chars": 0})
    ]


def test_empty_url_returns_error_dict() -> None:
    out = make_web_fetch(_FakeFetcher(_ARTICLE))("   ")

    assert out["status"] == "error"
    assert "empty" in out["error_message"]


def test_blocked_url_not_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wf, "_resolve_ips", _resolve_to("127.0.0.1"))
    fetcher = _FakeFetcher(_ARTICLE)

    out = make_web_fetch(fetcher)("http://localhost/")

    assert out["status"] == "error"
    assert "blocked address" in out["error_message"]
    assert fetcher.calls == []  # never reached the fetcher


def test_max_bytes_capped_and_floored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wf, "_resolve_ips", _resolve_to("93.184.216.34"))
    fetcher = _FakeFetcher(_ARTICLE)
    web_fetch = make_web_fetch(fetcher)

    web_fetch("https://example.com", max_bytes=10**12)
    web_fetch("https://example.com", max_bytes=0)

    assert [c[1] for c in fetcher.calls] == [MAX_BYTES_CAP, 1]


def test_empty_extraction_returns_error_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wf, "_resolve_ips", _resolve_to("93.184.216.34"))

    out = make_web_fetch(_FakeFetcher("<html><body></body></html>"))("https://example.com")

    assert out["status"] == "error"
    assert "readable content" in out["error_message"]


# --- redirect loop -----------------------------------------------------------------


def test_redirect_to_blocked_host_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # First host public, redirect target resolves to loopback.
    def resolver(host: str) -> list[str]:
        return ["93.184.216.34"] if host == "example.com" else ["127.0.0.1"]

    monkeypatch.setattr(wf, "_resolve_ips", resolver)

    def send(url: str) -> tuple[int, dict[str, str], str]:
        return 302, {"location": "http://internal.test/secret"}, ""

    with pytest.raises(BlockedURLError, match="blocked address"):
        wf._follow_redirects("https://example.com/start", send)


def test_redirect_followed_to_allowed_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wf, "_resolve_ips", _resolve_to("93.184.216.34"))
    pages = iter(
        [
            (302, {"location": "https://example.com/final"}, ""),
            (200, {}, "<html>done</html>"),
        ]
    )

    def send(url: str) -> tuple[int, dict[str, str], str]:
        return next(pages)

    out = wf._follow_redirects("https://example.com/start", send)

    assert out == {"final_url": "https://example.com/final", "html": "<html>done</html>"}


def test_too_many_redirects_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wf, "_resolve_ips", _resolve_to("93.184.216.34"))

    def send(url: str) -> tuple[int, dict[str, str], str]:
        return 302, {"location": "https://example.com/loop"}, ""

    with pytest.raises(BlockedURLError, match="too many redirects"):
        wf._follow_redirects("https://example.com/start", send)
