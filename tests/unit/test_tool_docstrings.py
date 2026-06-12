"""Regression guard for tool-schema overhead (issue #89).

ADK sends each tool's **entire docstring** to the model on every request
(``declaration.description`` is ``inspect.cleandoc(func.__doc__)``, verbatim), while
the JSON schema already carries types/defaults/required. So docstrings must stay
lean: no ``Returns:`` block (the model reads the runtime dict) and within a small
char budget. This test rebuilds every tool closure with fakes — construction needs
no binaries, browsers, or keys — and fails if the verbose pattern creeps back.
"""

from __future__ import annotations

import inspect
from typing import Any, cast

import pytest

from gaia.souls.delegate import make_delegate
from gaia.tools import browser, fs, shell
from gaia.tools.remember import make_remember
from gaia.tools.web_fetch import httpx_fetcher, make_web_fetch
from gaia.tools.web_search import make_web_search

#: Max docstring chars per tool (~175 tokens). The worst offender today is ~520.
DOC_BUDGET = 700


def _all_tools() -> dict[str, Any]:
    manager = browser.BrowserSessionManager()
    procs = shell.ProcessManager()
    return {
        "web_fetch": make_web_fetch(httpx_fetcher),
        "web_search": make_web_search(cast(Any, object())),
        "remember": make_remember(),
        "fs_read": fs.make_fs_read("/tmp/x"),
        "fs_write": fs.make_fs_write("/tmp/x"),
        "fs_edit": fs.make_fs_edit("/tmp/x"),
        "fs_glob": fs.make_fs_glob("/tmp/x"),
        "fs_grep": fs.make_fs_grep("/tmp/x"),
        "browser_navigate": browser.make_browser_navigate(manager),
        "browser_snapshot": browser.make_browser_snapshot(manager),
        "browser_click": browser.make_browser_click(manager),
        "browser_type": browser.make_browser_type(manager),
        "browser_screenshot": browser.make_browser_screenshot(manager),
        "exec": shell.make_exec(
            procs, shell.local_spawner, security="allowlist", allowlist=shell.DEFAULT_ALLOWLIST
        ),
        "exec_poll": shell.make_exec_poll(procs),
        "exec_kill": shell.make_exec_kill(procs),
        "exec_list": shell.make_exec_list(procs),
        # Construction only closes over the gaia object; a stub is enough here.
        "delegate_to_soul": make_delegate(cast(Any, object())),
    }


@pytest.mark.parametrize(("name", "fn"), sorted(_all_tools().items()))
def test_docstring_is_lean(name: str, fn: Any) -> None:
    doc = inspect.getdoc(fn)

    assert doc, f"{name} has no docstring (ADK needs one for the tool description)"
    assert "Returns:" not in doc, (
        f"{name} documents its return value — drop the Returns: block; the model "
        "reads the runtime result dict (issue #89)"
    )
    assert len(doc) <= DOC_BUDGET, (
        f"{name} docstring is {len(doc)} chars (> {DOC_BUDGET}) — it is sent to the "
        "model on every request; trim it (issue #89)"
    )
