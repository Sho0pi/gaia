"""Public-URL tunnel: provider specs, TunnelManager lifecycle, serve(public=True)."""

from __future__ import annotations

import asyncio

import pytest

from gaia.tools.serve import StaticServerManager  # noqa: F401 - parity import
from gaia.tools.serve.serve import make_serve, make_serve_stop
from gaia.tools.serve.tunnel import (
    TunnelError,
    TunnelManager,
    localtunnel_spec,
    pinggy_spec,
)


def test_pinggy_spec_argv_and_url_regex() -> None:
    spec = pinggy_spec(5173)
    assert spec.argv[0] == "ssh" and "0:localhost:5173" in spec.argv
    # Critical anti-hang flags present.
    assert "BatchMode=yes" in spec.argv and "StrictHostKeyChecking=no" in spec.argv
    assert spec.url_re.search("https://uljtt-30-47-152-61.run.pinggy-free.link")


def test_localtunnel_spec_argv_and_url_regex() -> None:
    spec = localtunnel_spec(8080, runtime="bunx")
    assert spec.argv == ["bunx", "localtunnel", "--port", "8080"]
    assert spec.url_re.search("your url is: https://flkajsfljas.loca.lt")


class _FakeProc:
    """Stand-in asyncio subprocess: emits given stdout lines, then blocks (no EOF)."""

    def __init__(self, lines: list[bytes], *, hang: bool = False) -> None:
        self._lines = list(lines)
        self._hang = hang
        self.returncode: int | None = None
        self.stdout = self
        self.terminated = False

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        if self._hang:
            await asyncio.sleep(3600)  # never returns -> exercises the timeout path
        return b""  # EOF

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


def _patch_spawn(monkeypatch: pytest.MonkeyPatch, proc: _FakeProc) -> None:
    async def fake_exec(*_a: object, **_k: object) -> _FakeProc:
        return proc

    monkeypatch.setattr("gaia.tools.serve.tunnel.asyncio.create_subprocess_exec", fake_exec)


async def test_open_returns_url_and_keeps_proc(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _FakeProc([b"setup...\n", b"https://abc.run.pinggy-free.link\n"])
    _patch_spawn(monkeypatch, proc)
    mgr = TunnelManager(provider="pinggy", timeout_seconds=5)
    url = await mgr.open(5173)
    assert url == "https://abc.run.pinggy-free.link"
    assert mgr.get(5173) is not None and not proc.terminated  # kept alive


async def test_open_is_idempotent_per_port(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _FakeProc([b"https://abc.run.pinggy-free.link\n"])
    _patch_spawn(monkeypatch, proc)
    mgr = TunnelManager(provider="pinggy", timeout_seconds=5)
    a = await mgr.open(5173)
    b = await mgr.open(5173)
    assert a == b


async def test_open_times_out_and_kills_proc(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _FakeProc([b"booting...\n"], hang=True)
    _patch_spawn(monkeypatch, proc)
    mgr = TunnelManager(provider="pinggy", timeout_seconds=0.2)
    with pytest.raises(TunnelError):
        await mgr.open(5173)
    assert proc.returncode is not None  # killed, no leak


async def test_unknown_provider_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_spawn(monkeypatch, _FakeProc([]))
    mgr = TunnelManager(provider="bogus", timeout_seconds=1)
    with pytest.raises(TunnelError):
        await mgr.open(5173)


# --- serve(public=...) integration with a fake tunnel ------------------------------------


class _FakeServer:
    def __init__(self, port: int = 4321) -> None:
        self.port = port
        self.root = "/ws"

    async def serve(self, _path: str) -> tuple[object, str]:
        return self, f"http://127.0.0.1:{self.port}/"

    async def stop(self, _target: str) -> object:
        return self


class _FakeTunnel:
    def __init__(self) -> None:
        self.closed: list[int] = []

    async def open(self, port: int) -> str:
        return f"https://pub-{port}.loca.lt"

    async def close(self, port: int) -> None:
        self.closed.append(port)

    def get(self, _port: int) -> None:
        return None


async def test_serve_public_disabled_reports_error() -> None:
    serve = make_serve(_FakeServer(), _FakeTunnel(), tunnel_enabled=False)  # type: ignore[arg-type]
    out = await serve("/ws", public=True)
    assert out["status"] == "success"
    assert "public_url" not in out and "disabled" in out["public_url_error"]


async def test_serve_public_enabled_returns_url() -> None:
    serve = make_serve(_FakeServer(), _FakeTunnel(), tunnel_enabled=True)  # type: ignore[arg-type]
    out = await serve("/ws", public=True)
    assert out["public_url"] == "https://pub-4321.loca.lt"


async def test_serve_stop_closes_tunnel() -> None:
    tun = _FakeTunnel()
    stop = make_serve_stop(_FakeServer(), tun)  # type: ignore[arg-type]
    out = await stop("4321")
    assert out["status"] == "success" and tun.closed == [4321]
