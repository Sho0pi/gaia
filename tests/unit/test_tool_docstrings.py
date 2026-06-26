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

from gaia.souls.consult import make_consult_soul
from gaia.souls.delegate import make_delegate
from gaia.tools import browser, fs, serve, shell
from gaia.tools.ask_user import make_ask_user
from gaia.tools.cron import make_cron
from gaia.tools.message import make_message_user
from gaia.tools.remember import make_remember
from gaia.tools.send_file import make_send_file
from gaia.tools.task import (
    make_task_complete,
    make_task_create,
    make_task_get,
    make_task_list,
    make_task_plan,
    make_task_update,
)
from gaia.tools.web_fetch import httpx_fetcher, make_web_fetch
from gaia.tools.web_search import make_web_search

#: Max docstring chars per tool (~175 tokens). The worst offender today is ~520.
DOC_BUDGET = 700


def _all_tools() -> dict[str, Any]:
    # Every tool's closure binds its deps at construction but never calls them here, so a bare
    # object() stands in for managers/stores/gaia. This covers the WHOLE surface — every tool's
    # docstring is sent to the model on every request, so none may exceed the budget.
    stub = cast(Any, object())
    manager = browser.BrowserSessionManager()
    procs = shell.ProcessManager()
    return {
        "web_fetch": make_web_fetch(httpx_fetcher),
        "web_search": make_web_search(stub),
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
        "serve": serve.make_serve(stub, None, tunnel_enabled=False),
        "serve_stop": serve.make_serve_stop(stub),
        "serve_list": serve.make_serve_list(stub),
        "task_create": make_task_create(stub),
        "task_plan": make_task_plan(stub),
        "task_list": make_task_list(stub),
        "task_get": make_task_get(stub),
        "task_update": make_task_update(stub),
        "task_complete": make_task_complete(stub),
        "cron": make_cron(stub),
        "ask_user": make_ask_user(),
        "send_file": make_send_file(),
        "message_user": make_message_user(stub, {}, lambda: None),
        "consult_soul": make_consult_soul(stub),
        "delegate_to_soul": make_delegate(stub),
    }


@pytest.mark.parametrize(("name", "fn"), sorted(_all_tools().items()))
def test_docstring_is_lean(name: str, fn: Any) -> None:
    # A BaseTool (e.g. ask_user's LongRunningFunctionTool) carries the docstring on its .func;
    # plain callables are their own docstring source.
    doc = inspect.getdoc(getattr(fn, "func", fn))

    assert doc, f"{name} has no docstring (ADK needs one for the tool description)"
    assert "Returns:" not in doc, (
        f"{name} documents its return value — drop the Returns: block; the model "
        "reads the runtime result dict (issue #89)"
    )
    assert len(doc) <= DOC_BUDGET, (
        f"{name} docstring is {len(doc)} chars (> {DOC_BUDGET}) — it is sent to the "
        "model on every request; trim it (issue #89)"
    )
