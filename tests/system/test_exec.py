"""System test: the exec tools run real subprocesses end to end.

No external service needed, but marked ``system`` because it spawns real OS processes
(and the background path relies on a POSIX shell). Runs trivially fast.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gaia.tools import shell
from gaia.tools.shell.base import ProcessManager, local_spawner

pytestmark = pytest.mark.system


class _Ctx:
    agent_name = "sys-exec"


@pytest.fixture(autouse=True)
def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gaia.constants.AGENTS_DIR", tmp_path / "agents")


async def test_foreground_echo_runs_for_real() -> None:
    tool = shell.make_exec(ProcessManager(), local_spawner, security="off")

    result = await tool("echo hello-from-exec", tool_context=_Ctx())

    assert result["status"] == "success"
    assert result["exit_code"] == 0
    assert "hello-from-exec" in result["stdout"]


async def test_background_process_polls_to_completion_no_orphan() -> None:
    mgr = ProcessManager()
    exec_tool = shell.make_exec(mgr, local_spawner, security="off")
    poll = shell.make_exec_poll(mgr)
    ctx: Any = _Ctx()

    started = await exec_tool(
        "sh -c 'echo a; sleep 0.2; echo b'", background=True, tool_context=ctx
    )
    assert started["status"] == "running"
    pid = started["process_id"]

    # Wait for it to finish by draining its reader, then poll.
    managed = mgr.get("sys-exec", pid)
    assert managed is not None
    await managed._pump_task  # type: ignore[arg-type]

    result = await poll(pid, tool_context=ctx)
    assert result["status"] == "exited"
    assert result["exit_code"] == 0
    assert "a" in result["output"] and "b" in result["output"]
    assert Path(started["log"]).is_file()  # noqa: ASYNC240 - assertion, not hot-path I/O

    await mgr.close_all()  # leaves no live child


async def test_config_allowlist_widens_and_denylist_still_applies() -> None:
    # tools.exec.allowlist WIDENS the built-in set: an extra command runs AND the defaults stay,
    # while the denylist keeps refusing destructive commands regardless. Guards the widen fix.
    allowlist = shell.widen_allowlist(["true"])  # 'true' is not a default; 'echo' is
    tool = shell.make_exec(
        ProcessManager(), local_spawner, security="allowlist", allowlist=allowlist
    )
    ctx: Any = _Ctx()

    assert (await tool("echo kept", tool_context=ctx))["status"] == "success"  # default kept
    assert (await tool("true", tool_context=ctx))["status"] == "success"  # widened-in extra
    assert (await tool("frobnicate x", tool_context=ctx))["status"] == "error"  # not allowed

    danger = await tool("rm -rf /", tool_context=ctx)
    assert danger["status"] == "error" and "denylist" in danger["error_message"]
