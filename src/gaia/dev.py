"""Developer web UI: serve Gaia through ADK's bundled dev console.

``gaia dev`` starts ADK's web UI (``get_fast_api_app(web=True)``) with Gaia as the
agent. Unlike the chat connectors — which only show the final reply — the UI exposes the
whole turn: every tool/function call + response, the LLM request/response, and a trace view.
The best surface for seeing *which* tools actually fire and *what* gets sent to the model.

ADK normally discovers agents from a folder of modules; we skip that with a tiny
:class:`BaseAgentLoader` that hands back :meth:`Gaia.build_root_agent` directly. All ADK /
server imports are deferred so the cli/whatsapp paths never pull fastapi/uvicorn.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from gaia import constants

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.core.agent import Gaia

logger = logging.getLogger(__name__)

#: The single agent name exposed in the dev UI.
AGENT_NAME = "gaia"


def make_agent_loader(gaia: Gaia) -> Any:
    """Build an ADK ``BaseAgentLoader`` that serves Gaia's root agent (built once)."""
    from google.adk.cli.utils.base_agent_loader import BaseAgentLoader

    class GaiaAgentLoader(BaseAgentLoader):
        """Returns Gaia's root agent for the dev UI instead of loading from disk."""

        def __init__(self) -> None:
            self._gaia = gaia
            self._root: Any | None = None

        def load_agent(self, agent_name: str) -> Any:
            if self._root is None:
                self._root = self._gaia.build_root_agent()
            return self._root

        def list_agents(self) -> list[str]:
            return [AGENT_NAME]

    return GaiaAgentLoader()


def serve_dev(gaia: Gaia, *, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the ADK dev web UI on ``gaia`` until interrupted. Blocks."""
    import uvicorn
    from google.adk.cli.fast_api import get_fast_api_app

    agents_dir = constants.HOME_DIR / "dev"
    agents_dir.mkdir(parents=True, exist_ok=True)

    app = get_fast_api_app(
        agents_dir=str(agents_dir),
        agent_loader=make_agent_loader(gaia),
        web=True,
        host=host,
        port=port,
    )
    logger.info("dev web UI on http://%s:%s", host, port)
    print(f"gaia dev UI: http://{host}:{port}  (agent: {AGENT_NAME})")
    uvicorn.run(app, host=host, port=port)
