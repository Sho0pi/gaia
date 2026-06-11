"""Two-tier memory: short-term is ADK's own session state; long-term is mem0.

Short-term (the per-session scratchpad) is handled directly by ADK's session service,
so there is no gaia class for it. Long-term is mem0, wrapped as an ADK
:class:`~gaia.memory.service.Mem0MemoryService` so it plugs straight into a ``Runner``.
"""

from gaia.memory.backend import build_mem0, build_mem0_config
from gaia.memory.service import Mem0MemoryService

__all__ = ["Mem0MemoryService", "build_mem0", "build_mem0_config"]
