"""Unit tests for the exec tools — driven with a fake spawner, no real subprocess."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from godpy.tools import shell
from godpy.tools.shell.base import ProcessManager, check_command, truncate


class _FakeStream:
    def __init__(self, chunks: list[bytes], done: asyncio.Event) -> None:
        self._chunks = list(chunks)
        self._done = done

    async def read(self, _n: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        await self._done.wait()  # block until the process ends (kill or auto-finish)
        return b""


class _FakeProc:
    """Mimics the slice of asyncio.subprocess.Process the manager + run_foreground use."""

    def __init__(
        self,
        *,
        chunks: list[bytes] | None = None,
        returncode: int = 0,
        auto_finish: bool = True,
        communicate_out: bytes = b"",
        hang: bool = False,
    ) -> None:
        self._done = asyncio.Event()
        self.stdout = _FakeStream(chunks or [], self._done)
        self._rc = returncode
        self._communicate_out = communicate_out
        self._hang = hang
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        if auto_finish:
            self._done.set()  # stdout EOFs right after its chunks → the process exits

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._hang:
            await asyncio.Event().wait()  # never returns → forces a timeout
        self.returncode = self._rc
        return self._communicate_out, b""

    async def wait(self) -> int:
        await self._done.wait()
        self.returncode = self._rc
        return self._rc

    def terminate(self) -> None:
        self.terminated = True
        self._done.set()

    def kill(self) -> None:
        self.killed = True
        self._done.set()


def _spawner(proc: _FakeProc) -> Any:
    async def spawn(_command: str, _cwd: Path, _env: dict[str, str] | None) -> _FakeProc:
        return proc

    return spawn


class _Ctx:
    def __init__(self, agent: str = "tester") -> None:
        self.agent_name = agent


@pytest.fixture(autouse=True)
def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # exec resolves cwd via sandbox_for(constants.AGENTS_DIR, agent); keep it in tmp.
    monkeypatch.setattr("godpy.constants.AGENTS_DIR", tmp_path / "agents")


# --- command safety (pure) --------------------------------------------------------


@pytest.mark.parametrize("security", ["off", "allowlist", "ask"])
def test_denylist_blocks_rm_rf_in_every_mode(security: str) -> None:
    error = check_command("rm -rf /", security=security, allowlist=shell.DEFAULT_ALLOWLIST)
    assert error is not None and "denylist" in error


def test_allowlist_blocks_unknown_binary() -> None:
    error = check_command("frobnicate x", security="allowlist", allowlist=("ls", "echo"))
    assert error is not None and "frobnicate" in error


def test_allowlist_blocks_chaining() -> None:
    error = check_command("echo a && echo b", security="allowlist", allowlist=("echo",))
    assert error is not None and "chaining" in error


def test_off_mode_allows_anything_not_denylisted() -> None:
    assert check_command("anything goes", security="off", allowlist=()) is None


def test_ask_mode_points_at_issue_29() -> None:
    error = check_command("echo hi", security="ask", allowlist=())
    assert error is not None and "#29" in error


def test_truncate_keeps_tail() -> None:
    text, cut = truncate("abcdef", cap=3)
    assert text == "def" and cut is True


# --- foreground exec --------------------------------------------------------------


async def test_foreground_success() -> None:
    proc = _FakeProc(communicate_out=b"hello\n", returncode=0)
    tool = shell.make_exec(ProcessManager(_spawner(proc)), _spawner(proc), security="off")

    result = await tool("echo hello", tool_context=_Ctx())

    assert result["status"] == "success"
    assert result["exit_code"] == 0
    assert result["stdout"] == "hello\n"


async def test_foreground_nonzero_exit_is_error() -> None:
    proc = _FakeProc(communicate_out=b"boom\n", returncode=2)
    tool = shell.make_exec(ProcessManager(_spawner(proc)), _spawner(proc), security="off")

    result = await tool("false", tool_context=_Ctx())

    assert result["status"] == "error"
    assert result["exit_code"] == 2
    assert result["stdout"] == "boom\n"  # streams still returned


async def test_foreground_timeout_is_killed() -> None:
    proc = _FakeProc(hang=True)
    tool = shell.make_exec(ProcessManager(_spawner(proc)), _spawner(proc), security="off")

    result = await tool("sleep 999", timeout_seconds=0.05, tool_context=_Ctx())

    assert result["status"] == "error"
    assert "timed out" in result["error_message"]
    assert proc.killed is True


async def test_denylisted_command_never_runs() -> None:
    proc = _FakeProc(communicate_out=b"")
    tool = shell.make_exec(ProcessManager(_spawner(proc)), _spawner(proc), security="off")

    result = await tool("rm -rf /", tool_context=_Ctx())

    assert result["status"] == "error" and "denylist" in result["error_message"]
    assert proc.returncode is None  # never spawned


async def test_workdir_escape_rejected() -> None:
    proc = _FakeProc(communicate_out=b"")
    tool = shell.make_exec(ProcessManager(_spawner(proc)), _spawner(proc), security="off")

    result = await tool("ls", workdir="../../etc", tool_context=_Ctx())

    assert result["status"] == "error" and "escapes" in result["error_message"]


# --- background exec + poll / kill / list -----------------------------------------


async def test_background_spawn_returns_id_and_writes_log() -> None:
    proc = _FakeProc(chunks=[b"line1\n"], returncode=0, auto_finish=True)
    mgr = ProcessManager(_spawner(proc))
    tool = shell.make_exec(mgr, _spawner(proc), security="off")

    result = await tool("python serve.py", background=True, tool_context=_Ctx())

    assert result["status"] == "running"
    assert result["process_id"] == "proc-1"
    managed = mgr.get("tester", "proc-1")
    assert managed is not None
    await managed._pump_task  # let the reader drain + tee
    assert Path(result["log"]).read_text() == "line1\n"  # noqa: ASYNC240 - assertion


async def test_poll_returns_output_then_exit() -> None:
    proc = _FakeProc(chunks=[b"abc"], returncode=0, auto_finish=True)
    mgr = ProcessManager(_spawner(proc))
    exec_tool = shell.make_exec(mgr, _spawner(proc), security="off")
    poll = shell.make_exec_poll(mgr)
    ctx = _Ctx()

    pid = (await exec_tool("run", background=True, tool_context=ctx))["process_id"]
    await mgr.get("tester", pid)._pump_task  # type: ignore[union-attr]

    result = await poll(pid, tool_context=ctx)

    assert result["status"] == "exited"
    assert result["exit_code"] == 0
    assert "abc" in result["output"]


async def test_poll_unknown_id_errors() -> None:
    poll = shell.make_exec_poll(ProcessManager())
    result = await poll("proc-99", tool_context=_Ctx())
    assert result["status"] == "error" and "unknown process" in result["error_message"]


async def test_poll_cross_agent_denied() -> None:
    proc = _FakeProc(chunks=[b"x"], auto_finish=True)
    mgr = ProcessManager(_spawner(proc))
    exec_tool = shell.make_exec(mgr, _spawner(proc), security="off")
    poll = shell.make_exec_poll(mgr)

    pid = (await exec_tool("run", background=True, tool_context=_Ctx("alice")))["process_id"]
    result = await poll(pid, tool_context=_Ctx("bob"))  # different agent

    assert result["status"] == "error" and "unknown process" in result["error_message"]


async def test_kill_stops_a_running_process() -> None:
    proc = _FakeProc(auto_finish=False)  # blocks until killed
    mgr = ProcessManager(_spawner(proc))
    exec_tool = shell.make_exec(mgr, _spawner(proc), security="off")
    kill = shell.make_exec_kill(mgr)
    ctx = _Ctx()

    pid = (await exec_tool("python -m http.server", background=True, tool_context=ctx))[
        "process_id"
    ]
    assert mgr.get("tester", pid).running is True  # type: ignore[union-attr]

    result = await kill(pid, tool_context=ctx)

    assert result["status"] == "success"
    assert proc.terminated is True
    assert mgr.get("tester", pid).running is False  # type: ignore[union-attr]


async def test_list_is_agent_scoped() -> None:
    proc = _FakeProc(chunks=[b"x"], auto_finish=True)
    mgr = ProcessManager(_spawner(proc))
    exec_tool = shell.make_exec(mgr, _spawner(proc), security="off")
    list_tool = shell.make_exec_list(mgr)

    await exec_tool("run", background=True, tool_context=_Ctx("alice"))

    alice = await list_tool(tool_context=_Ctx("alice"))
    bob = await list_tool(tool_context=_Ctx("bob"))

    assert len(alice["processes"]) == 1 and alice["processes"][0]["process_id"] == "proc-1"
    assert bob["processes"] == []


async def test_close_all_kills_everything() -> None:
    proc = _FakeProc(auto_finish=False)
    mgr = ProcessManager(_spawner(proc))
    exec_tool = shell.make_exec(mgr, _spawner(proc), security="off")
    await exec_tool("long", background=True, tool_context=_Ctx())

    await mgr.close_all()

    assert proc.terminated is True
    assert mgr.list("tester") == []
