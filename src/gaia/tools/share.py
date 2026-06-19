"""``share_file`` — the root-only tool that sends a file on disk to the user.

Outbound counterpart to inbound media: the model streams text, and screenshots ride out
automatically, but to deliver an arbitrary file (a generated image, a document a soul
produced, a file the user sent earlier) the model calls ``share_file(path)``. The tool just
validates the path and reports it; :func:`gaia.core.screenshots.media_for_outputs` turns that
result into an outbound :class:`~gaia.connectors.base.Media`, which the connector sends as the
right file type (see :func:`gaia.connectors.base.media_kind`).

Root-only (attached in :meth:`gaia.core.agent.Gaia.build_root_agent`): souls don't talk to the
user — the root relays — and the root's sandbox already roots the whole agents tree + uploads,
so it can reach any soul deliverable or uploaded file.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.tools.tool_context import ToolContext

from gaia import constants
from gaia.tools._helpers import err, ok

#: Tool id (matches the closure name); recognised by the outbound-media event scan.
NAME = "share_file"


def make_share_file() -> Callable[..., Awaitable[dict[str, Any]]]:
    """Return the root-only ``share_file`` tool."""

    async def share_file(
        path: str, caption: str = "", *, tool_context: ToolContext
    ) -> dict[str, Any]:
        """Send a file on disk to the user (image, video, audio, or document).

        Use this to deliver an actual file — an image you generated, a document a soul wrote,
        a file the user sent you — instead of just describing it or pasting a path. Put your
        message in ``caption`` (it rides with the file); don't also send it as a separate text
        reply. The file type is detected from the path, so the user gets a real photo/video/
        document, not a link.

        Args:
            path: path to the file to send (a deliverable in a soul's workspace, an uploaded
                file, or a screenshot/generated image — anything inside your sandbox).
            caption: optional message to send alongside the file.
        """
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

    return share_file
