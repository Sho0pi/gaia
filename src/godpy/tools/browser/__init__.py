"""Browser tools — ``browser_navigate``, ``browser_snapshot``, ``browser_click``,
``browser_type``, ``browser_screenshot``.

A stateful bundle: the tools share one headless Chromium page per agent, held by the
:class:`~godpy.tools.browser.base.BrowserSessionManager`. One file per tool;
:mod:`godpy.tools.browser.base` holds the session manager, the accessibility-snapshot
flattener, and the ref resolver. Playwright is imported lazily, so this package
imports without it; the registry only attaches the tools when Playwright is installed.
"""

from godpy.tools.browser.base import BrowserSessionManager, default_manager
from godpy.tools.browser.click import NAME as CLICK
from godpy.tools.browser.click import make_browser_click
from godpy.tools.browser.navigate import NAME as NAVIGATE
from godpy.tools.browser.navigate import make_browser_navigate
from godpy.tools.browser.screenshot import NAME as SCREENSHOT
from godpy.tools.browser.screenshot import make_browser_screenshot
from godpy.tools.browser.snapshot import NAME as SNAPSHOT
from godpy.tools.browser.snapshot import make_browser_snapshot
from godpy.tools.browser.type_text import NAME as TYPE
from godpy.tools.browser.type_text import make_browser_type

__all__ = [
    "CLICK",
    "NAVIGATE",
    "SCREENSHOT",
    "SNAPSHOT",
    "TYPE",
    "BrowserSessionManager",
    "default_manager",
    "make_browser_click",
    "make_browser_navigate",
    "make_browser_screenshot",
    "make_browser_snapshot",
    "make_browser_type",
]
