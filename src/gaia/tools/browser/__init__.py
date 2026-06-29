"""Browser tools — navigate, snapshot, click, type, screenshot, scroll, press, back,
get_images, console, dialog, evaluate (the hermes-agent surface).

A stateful bundle: the tools share one page per agent, held by the
:class:`~gaia.tools.browser.base.BrowserSessionManager`. The engine is chromium by default or
Camoufox (anti-detect Firefox) per ``browser.engine`` — see :func:`base.make_launcher`. One file
per tool; :mod:`gaia.tools.browser.base` holds the session manager, the accessibility-snapshot
flattener, and the ref resolver. Playwright is imported lazily, so this package imports without it;
the registry only attaches the tools when Playwright is installed.
"""

from gaia.tools.browser.back import NAME as BACK
from gaia.tools.browser.back import make_browser_back
from gaia.tools.browser.base import BrowserSessionManager, make_launcher
from gaia.tools.browser.click import NAME as CLICK
from gaia.tools.browser.click import make_browser_click
from gaia.tools.browser.console import NAME as CONSOLE
from gaia.tools.browser.console import make_browser_console
from gaia.tools.browser.dialog import NAME as DIALOG
from gaia.tools.browser.dialog import make_browser_dialog
from gaia.tools.browser.evaluate import NAME as EVALUATE
from gaia.tools.browser.evaluate import make_browser_evaluate
from gaia.tools.browser.get_images import NAME as GET_IMAGES
from gaia.tools.browser.get_images import make_browser_get_images
from gaia.tools.browser.navigate import NAME as NAVIGATE
from gaia.tools.browser.navigate import make_browser_navigate
from gaia.tools.browser.press import NAME as PRESS
from gaia.tools.browser.press import make_browser_press
from gaia.tools.browser.screenshot import NAME as SCREENSHOT
from gaia.tools.browser.screenshot import make_browser_screenshot
from gaia.tools.browser.scroll import NAME as SCROLL
from gaia.tools.browser.scroll import make_browser_scroll
from gaia.tools.browser.snapshot import NAME as SNAPSHOT
from gaia.tools.browser.snapshot import make_browser_snapshot
from gaia.tools.browser.type_text import NAME as TYPE
from gaia.tools.browser.type_text import make_browser_type

__all__ = [
    "BACK",
    "CLICK",
    "CONSOLE",
    "DIALOG",
    "EVALUATE",
    "GET_IMAGES",
    "NAVIGATE",
    "PRESS",
    "SCREENSHOT",
    "SCROLL",
    "SNAPSHOT",
    "TYPE",
    "BrowserSessionManager",
    "make_browser_back",
    "make_browser_click",
    "make_browser_console",
    "make_browser_dialog",
    "make_browser_evaluate",
    "make_browser_get_images",
    "make_browser_navigate",
    "make_browser_press",
    "make_browser_screenshot",
    "make_browser_scroll",
    "make_browser_snapshot",
    "make_browser_type",
    "make_launcher",
]
