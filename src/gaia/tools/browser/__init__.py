"""Browser tools — ``browser_navigate``, ``browser_snapshot``, ``browser_click``,
``browser_type``, ``browser_screenshot``.

A stateful bundle: the tools share one headless Chromium page per agent, held by the
:class:`~gaia.tools.browser.base.BrowserSessionManager`. One file per tool;
:mod:`gaia.tools.browser.base` holds the session manager, the accessibility-snapshot
flattener, and the ref resolver. Playwright is imported lazily, so this package
imports without it; the registry only attaches the tools when Playwright is installed.
"""

from gaia.tools.browser.base import BrowserSessionManager
from gaia.tools.browser.click import NAME as CLICK
from gaia.tools.browser.click import make_browser_click
from gaia.tools.browser.navigate import NAME as NAVIGATE
from gaia.tools.browser.navigate import make_browser_navigate
from gaia.tools.browser.screenshot import NAME as SCREENSHOT
from gaia.tools.browser.screenshot import make_browser_screenshot
from gaia.tools.browser.snapshot import NAME as SNAPSHOT
from gaia.tools.browser.snapshot import make_browser_snapshot
from gaia.tools.browser.type_text import NAME as TYPE
from gaia.tools.browser.type_text import make_browser_type

__all__ = [
    "CLICK",
    "NAVIGATE",
    "SCREENSHOT",
    "SNAPSHOT",
    "TYPE",
    "BrowserSessionManager",
    "make_browser_click",
    "make_browser_navigate",
    "make_browser_screenshot",
    "make_browser_snapshot",
    "make_browser_type",
]
