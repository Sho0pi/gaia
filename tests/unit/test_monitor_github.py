"""monitor/github: create a new issue, or comment on the existing one matching the signature."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from gaia.monitor import github


def _patch_client(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


async def test_creates_new_issue_when_none_match(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[])  # no open labelled issues
        seen["path"] = request.url.path
        seen["body"] = request.content.decode()
        return httpx.Response(201, json={"html_url": "https://github.com/o/r/issues/1"})

    _patch_client(monkeypatch, handler)
    url = await github.file_issue(
        "o/r", "tok", signature="KeyError @ h.py:1", title="bug", body="boom", label="gaia-monitor"
    )
    assert url.endswith("/issues/1")
    assert seen["path"] == "/repos/o/r/issues"  # POSTed a new issue
    assert github._marker("KeyError @ h.py:1") in seen["body"]  # marker embedded for future dedup


async def test_comments_when_signature_already_open(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = github._marker("KeyError @ h.py:1")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 7,
                        "html_url": "https://github.com/o/r/issues/7",
                        "body": f"old\n{marker}",
                    }
                ],
            )
        seen["path"] = request.url.path  # the POST (a comment, not a new issue)
        return httpx.Response(201, json={})

    _patch_client(monkeypatch, handler)
    url = await github.file_issue(
        "o/r", "tok", signature="KeyError @ h.py:1", title="bug", body="boom", label="gaia-monitor"
    )
    assert url.endswith("/issues/7")  # the existing issue, not a new one
    assert seen["path"] == "/repos/o/r/issues/7/comments"  # commented instead of duplicating


async def test_reopens_closed_issue_on_recurrence(monkeypatch: pytest.MonkeyPatch) -> None:
    # A signature whose issue was CLOSED (triaged/fixed) but recurred: reopen + comment, don't dupe.
    marker = github._marker("KeyError @ h.py:1")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 9,
                        "state": "closed",
                        "html_url": "https://github.com/o/r/issues/9",
                        "body": f"old\n{marker}",
                    }
                ],
            )
        if request.method == "PATCH":
            seen["reopened"] = request.url.path
            return httpx.Response(200, json={})
        seen["commented"] = request.url.path
        return httpx.Response(201, json={})

    _patch_client(monkeypatch, handler)
    url = await github.file_issue(
        "o/r", "tok", signature="KeyError @ h.py:1", title="bug", body="b", label="gaia-monitor"
    )
    assert url.endswith("/issues/9")  # the existing (reopened) issue, not a new one
    assert seen.get("reopened") == "/repos/o/r/issues/9"  # PATCH state=open
    assert seen.get("commented") == "/repos/o/r/issues/9/comments"
