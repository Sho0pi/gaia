"""Shell tools — ``exec`` plus the background-process trio ``exec_poll`` /
``exec_kill`` / ``exec_list``.

A stateful bundle: ``exec(background=True)`` spawns a long-lived process held by a
per-agent :class:`~godpy.tools.shell.base.ProcessManager` (terminated on process exit,
so nothing orphans), and the trio polls/stops/lists them. One file per tool;
:mod:`godpy.tools.shell.base` holds the manager, command-safety policy, and the
spawner seam. The package is named ``shell`` (not ``exec``) to avoid shadowing the
``exec`` builtin in imports; the tool ids are still ``exec`` / ``exec_*``.
"""

from godpy.tools.shell.base import (
    DEFAULT_ALLOWLIST,
    ProcessManager,
    local_spawner,
)
from godpy.tools.shell.kill import NAME as KILL
from godpy.tools.shell.kill import make_exec_kill
from godpy.tools.shell.list_procs import NAME as LIST
from godpy.tools.shell.list_procs import make_exec_list
from godpy.tools.shell.poll import NAME as POLL
from godpy.tools.shell.poll import make_exec_poll
from godpy.tools.shell.run import NAME as EXEC
from godpy.tools.shell.run import make_exec

__all__ = [
    "DEFAULT_ALLOWLIST",
    "EXEC",
    "KILL",
    "LIST",
    "POLL",
    "ProcessManager",
    "local_spawner",
    "make_exec",
    "make_exec_kill",
    "make_exec_list",
    "make_exec_poll",
]
