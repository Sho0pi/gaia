"""``send_file`` — the root-only tool for delivering a file to the user.

The model streams text and screenshots ride out on their own; to deliver an arbitrary file
(a generated image, a soul's deliverable, a file the user sent) the model calls
``send_file(path)``. Like the screenshot path, the tool itself only validates the path and
reports it — tools can't reach the connector's send sink — and
:func:`gaia.core.screenshots.media_for_outputs` turns that result into an outbound
:class:`~gaia.connectors.base.Media` the connector sends as the right file type.

Root-only (attached in :meth:`gaia.core.agent.Gaia.build_root_agent`): souls don't talk to
the user — the root relays — and the root's sandbox roots the whole agents tree + uploads, so
it can reach any soul deliverable or uploaded file.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia import constants
from gaia.tools._helpers import err, ok

#: Tool id (matches the closure name); recognised by the outbound-media event scan.
NAME = "send_file"


def make_send_file() -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the root-only ``send_file`` tool."""

    async def send_file(
        path: str, caption: str = "", *, tool_context: ToolContext
    ) -> dict[str, Any]:
        """Deliver a file on disk to the user — image, video, audio, document, or zip.

        Use this to actually send a file, not describe it, paste a path, or serve a link
        (``serve`` is only for previewing a website). Put your message in ``caption``. Sends
        one file per call — for several, zip them and send the zip.

        Args:
            path: the file to send (anything inside your sandbox).
            caption: optional message sent with the file.
        """
        path, caption = path or "", caption or ""  # a model may send null, not the default
        # No self-logging: ToolLoggingPlugin records one tool_used event per call.
        from gaia.connectors.base import media_kind
        from gaia.tools.fs.base import SandboxError, sandbox_for

        try:
            resolved = sandbox_for(constants.AGENTS_DIR, tool_context.agent_name).resolve(path)
        except SandboxError as exc:
            return err(str(exc))
        if not resolved.is_file():
            return err(f"not a file: {path}")
        return ok(path=str(resolved), caption=caption, kind=media_kind(resolved))

    return send_file
