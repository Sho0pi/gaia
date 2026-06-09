"""Filesystem tools — ``fs_read``, ``fs_write``, ``fs_edit``, ``fs_glob``, ``fs_grep``.

Ported in spirit from ``Sho0pi/agenttools`` ``fs/`` (same params + safety, ADK
function-tool idiom). One file per tool; :mod:`godpy.tools.fs.base` holds the shared
:class:`Sandbox` and path-safety helpers. Each tool reads the calling agent from ADK's
injected ``tool_context.agent_name`` and confines paths to that agent's workspace
(``~/.godpy/agents/<agent>/workspace``) plus a scoped scratch dir (``/tmp/godpy/<agent>``).
"""

from godpy.tools.fs.base import Sandbox, SandboxError
from godpy.tools.fs.edit import NAME as EDIT
from godpy.tools.fs.edit import make_fs_edit
from godpy.tools.fs.glob import NAME as GLOB
from godpy.tools.fs.glob import make_fs_glob
from godpy.tools.fs.grep import NAME as GREP
from godpy.tools.fs.grep import make_fs_grep
from godpy.tools.fs.read import NAME as READ
from godpy.tools.fs.read import make_fs_read
from godpy.tools.fs.write import NAME as WRITE
from godpy.tools.fs.write import make_fs_write

__all__ = [
    "EDIT",
    "GLOB",
    "GREP",
    "READ",
    "WRITE",
    "Sandbox",
    "SandboxError",
    "make_fs_edit",
    "make_fs_glob",
    "make_fs_grep",
    "make_fs_read",
    "make_fs_write",
]
