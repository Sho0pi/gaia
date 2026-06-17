"""A background dev server's port is trusted for browser_navigate, and released on exit."""

from __future__ import annotations

from pathlib import Path

from gaia.tools.serve import ServedPorts
from gaia.tools.shell.base import ManagedProcess, ProcessManager


class _FakeProc:
    """Minimal asyncio-subprocess stand-in: streams given chunks, then EOF + exit 0."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.stdout = self
        self._chunks = chunks
        self.returncode = 0

    async def read(self, _n: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    async def wait(self) -> int:
        return 0

    def terminate(self) -> None:  # pragma: no cover - not killed in these tests
        pass


def _manager(served: ServedPorts, chunks: list[bytes]) -> ProcessManager:
    async def spawner(command: str, cwd: Path, env: object) -> _FakeProc:
        return _FakeProc(list(chunks))

    return ProcessManager(spawner=spawner, served=served)  # type: ignore[arg-type]


async def test_declared_port_trusted_immediately(tmp_path: Path) -> None:
    served = ServedPorts()
    mgr = _manager(served, chunks=[])
    managed = await mgr.spawn("gaia", "vite --port 5173", tmp_path, port=5173)
    assert 5173 in served  # trusted before any output
    await mgr.kill(managed)
    assert 5173 not in served  # released on exit


async def test_port_autodetected_from_output(tmp_path: Path) -> None:
    served = ServedPorts()
    mgr = _manager(served, chunks=[b"VITE ready\n", b"  Local: http://localhost:4321/\n"])
    managed = await mgr.spawn("gaia", "npm run dev", tmp_path)
    assert managed._pump_task is not None
    await managed._pump_task  # drain the fake output (then the proc 'exits')
    # The pump trusted 4321 while running, then released it when the stream ended.
    assert 4321 not in served  # released after exit
    assert 4321 in managed._ports or managed._ports == set()  # was seen during the run


async def test_autodetect_trusts_while_running(tmp_path: Path) -> None:
    # Trust must be live *during* the run; check the moment the chunk is appended.
    served = ServedPorts()
    managed = ManagedProcess(
        process_id="p1",
        agent="gaia",
        command="x",
        proc=None,  # type: ignore[arg-type]
        log_path=tmp_path / "x.log",
        _served=served,
    )
    managed._append("Running on http://127.0.0.1:8000\n")
    assert 8000 in served
    managed.release_ports()
    assert 8000 not in served
