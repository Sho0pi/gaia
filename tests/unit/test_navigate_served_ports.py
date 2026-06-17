"""browser_navigate trusts our own served loopback ports, but no other local address."""

from __future__ import annotations

from gaia.tools.browser.navigate import _served_loopback
from gaia.tools.serve import ServedPorts


def test_served_loopback_port_is_trusted() -> None:
    served = ServedPorts()
    served.add(49000)
    assert _served_loopback("http://127.0.0.1:49000/index.html", served) is True
    assert _served_loopback("http://localhost:49000/", served) is True


def test_unserved_loopback_port_is_not_trusted() -> None:
    served = ServedPorts()
    served.add(49000)
    # A different local port (e.g. some other service) is not ours -> not trusted.
    assert _served_loopback("http://127.0.0.1:5432/", served) is False


def test_non_loopback_never_trusted() -> None:
    served = ServedPorts()
    served.add(49000)
    assert _served_loopback("http://example.com:49000/", served) is False
    assert _served_loopback("http://169.254.169.254/", served) is False


def test_no_served_set_means_no_allowance() -> None:
    assert _served_loopback("http://127.0.0.1:49000/", None) is False
