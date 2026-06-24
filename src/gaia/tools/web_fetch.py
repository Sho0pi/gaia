"""The ``web_fetch`` tool: fetch a URL and return its main content as markdown.

Two concerns, kept separate so each is testable on its own:

* **SSRF guard** (:func:`validate_url`) — a pure, DNS-aware check that rejects anything
  that could reach the host's own network: non-http(s) schemes, embedded credentials,
  and any URL whose host resolves to a loopback / private / link-local / metadata /
  CGNAT / multicast address. It is *re-applied on every redirect hop* (pragmatic
  re-validate: a small DNS-rebinding TOCTOU window remains by design — see issue #24).
* **Fetcher** (:class:`Fetcher`) — the HTTP backend that follows redirects (revalidating
  each hop) and returns raw HTML. The default :func:`httpx_fetcher` imports ``httpx``
  lazily (heavy-deps convention) so importing this module pulls in no HTTP stack.

The HTML→markdown conversion happens in the tool closure via ``trafilatura`` (also a
lazy import), which extracts the readable article and drops nav/boilerplate.

Unlike ``web_search``, ``web_fetch`` needs no configuration — it is on by default.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import urlparse, urlsplit

from gaia.tools._helpers import err, ok

#: Tool id, used by the registry and as the ADK tool name (matches the closure name).
NAME = "web_fetch"

#: Default and hard cap for the number of response bytes read.
DEFAULT_MAX_BYTES = 2_000_000
MAX_BYTES_CAP = 10_000_000

#: How many redirects to follow before giving up.
MAX_REDIRECTS = 5


def _user_agent() -> str:
    """``gaia/<version>`` UA, falling back to ``gaia/0`` when the package isn't installed."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return f"gaia/{version('gaia')} (+https://github.com/Sho0pi/gaia)"
    except PackageNotFoundError:  # pragma: no cover - editable/uninstalled
        return "gaia/0 (+https://github.com/Sho0pi/gaia)"


#: Browser-like request headers — many sites 403 a header-less client.
_DEFAULT_HEADERS = {"User-Agent": _user_agent(), "Accept": "text/html,*/*"}

#: Only these URL schemes are ever fetched.
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Networks an agent must never be able to reach via a fetched URL. ``ipaddress`` flags
#: cover most of these, but CGNAT (RFC 6598) is neither ``is_private`` nor reserved, so
#: it is listed explicitly. Cloud metadata (169.254.169.254) falls under link-local.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class BlockedURLError(Exception):
    """Raised by a fetcher when a URL (initial or via redirect) targets a blocked host."""


def _ip_blocked(ip: str) -> bool:
    """True if ``ip`` is loopback / private / link-local / multicast / reserved / CGNAT."""
    addr = ipaddress.ip_address(ip)
    return (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or addr in _CGNAT
    )


def _resolve_ips(host: str) -> list[str]:
    """Resolve ``host`` to its IP addresses (monkeypatched in tests)."""
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def validate_url(url: str) -> str | None:
    """Check a URL is safe to fetch; return an error message, or ``None`` if allowed.

    Rejects non-http(s) schemes, URLs carrying embedded credentials, and any host that
    resolves (now) to a non-public address. Re-run this on every redirect target.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return f"scheme must be http or https, got {parsed.scheme!r}"
    if parsed.username or parsed.password:
        return "embedded credentials in the URL are not allowed"
    host = parsed.hostname
    if not host:
        return "URL has no host"
    try:
        ips = _resolve_ips(host)
    except OSError as exc:
        return f"could not resolve host {host!r}: {exc}"
    if not ips:
        return f"could not resolve host {host!r}"
    blocked = [ip for ip in ips if _ip_blocked(ip)]
    if blocked:
        return f"host {host!r} resolves to a blocked address: {blocked[0]}"
    return None


class Fetcher(Protocol):
    """An HTTP backend: given a (validated) URL, return the final URL and its HTML."""

    def __call__(self, url: str, max_bytes: int) -> dict[str, str]: ...


def _follow_redirects(
    url: str,
    send: Callable[[str], tuple[int, dict[str, str], str]],
) -> dict[str, str]:
    """Drive the request/redirect loop, revalidating every hop.

    ``send`` performs one request and returns ``(status_code, headers, text)``; it is a
    seam so the loop can be exercised with a fake sender (no network) in tests. Each hop
    is checked with :func:`validate_url` before it is sent; a blocked hop raises
    :class:`BlockedURLError`.
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        error = validate_url(current)
        if error is not None:
            raise BlockedURLError(error)
        status, headers, text = send(current)
        if status in (301, 302, 303, 307, 308):
            location = headers.get("location") or headers.get("Location")
            if not location:
                return {"final_url": current, "html": text}
            current = _resolve_location(current, location)
            continue
        return {"final_url": current, "html": text}
    raise BlockedURLError(f"too many redirects (>{MAX_REDIRECTS})")


def _resolve_location(base: str, location: str) -> str:
    """Resolve a (possibly relative) redirect ``location`` against ``base``."""
    if urlsplit(location).scheme:
        return location
    from urllib.parse import urljoin

    return urljoin(base, location)


def httpx_fetcher(url: str, max_bytes: int) -> dict[str, str]:
    """Default :class:`Fetcher`: fetch ``url`` with ``httpx``, revalidating each redirect.

    Redirects are followed manually (``follow_redirects=False``) so :func:`validate_url`
    runs on every hop; at most ``max_bytes`` of the body are read.
    """
    import httpx

    # Many sites 403 a bare client; send a real User-Agent + Accept like a browser would.
    with httpx.Client(follow_redirects=False, timeout=30.0, headers=_DEFAULT_HEADERS) as client:

        def send(target: str) -> tuple[int, dict[str, str], str]:
            with client.stream("GET", target) as response:
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) >= max_bytes:
                        body = body[:max_bytes]
                        break
                text = body.decode(response.encoding or "utf-8", errors="replace")
                return response.status_code, dict(response.headers), text

        return _follow_redirects(url, send)


def make_web_fetch(fetcher: Fetcher) -> Callable[..., dict[str, Any]]:
    """Return the ADK web_fetch tool bound to ``fetcher``.

    ADK reads the returned function's name, signature and docstring to build the tool
    schema, so the closure's name matches :data:`NAME` and documents its args + return.
    """

    def web_fetch(url: str, max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, Any]:
        """Fetch a web page and return its readable content as markdown (nav/ads stripped).

        Args:
            url: the http(s) URL to fetch.
        """
        cleaned = url.strip()

        if not cleaned:
            return err("url must not be empty")

        error = validate_url(cleaned)
        if error is not None:
            return err(error)

        capped = max(1, min(max_bytes, MAX_BYTES_CAP))
        try:
            fetched = fetcher(cleaned, capped)
        except BlockedURLError as exc:
            return err(f"refused: {exc}")
        except Exception as exc:
            return err(f"fetch failed: {exc}")

        import trafilatura

        markdown = trafilatura.extract(fetched["html"], output_format="markdown")
        if not markdown:
            return err("no readable content could be extracted from the page")
        return ok(url=fetched["final_url"], markdown=markdown)

    return web_fetch
