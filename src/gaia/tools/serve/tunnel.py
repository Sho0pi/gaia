"""Public-URL tunnels for the serve tools: expose a local server on the internet.

``serve`` hosts a built site on ``http://127.0.0.1:<port>``; a tunnel forwards that port to
a public https URL so the user can open it on their phone / share it for live testing.
Pluggable backend (like the browser native/mcp split):

* **pinggy** (default) — ``ssh -R`` to free.pinggy.io, zero install (ssh is everywhere),
  prints ``https://<rand>.run.pinggy-free.link``.
* **localtunnel** — ``bunx localtunnel`` (reuses the bun runtime already used for the MCP
  browser), prints ``your url is: https://<rand>.loca.lt``.

Both are long-lived subprocesses that print the public URL to stdout; :class:`TunnelManager`
spawns one per port, reads the URL out (with a timeout), keeps it alive, and tears it down —
mirroring :class:`gaia.tools.serve.base.StaticServerManager`. Off by default: public
exposure is gated by ``tools.serve.tunnel.enabled`` and ships the served content + the
daemon's egress to the internet.
"""

from __future__ import annotations

import asyncio
import atexit
import re
from dataclasses import dataclass

#: Seconds to wait for a backend to print its public URL before giving up.
DEFAULT_TIMEOUT_SECONDS = 25.0

#: Grace period (s) for a tunnel subprocess to exit after terminate() before kill().
_KILL_GRACE = 5.0


class TunnelError(Exception):
    """Raised when a tunnel can't be opened (unknown provider, no URL, runtime missing)."""


@dataclass(frozen=True)
class TunnelSpec:
    """How to launch a provider and recognise the public URL in its output."""

    argv: list[str]
    url_re: re.Pattern[str]


#: ssh flags so the tunnel never blocks on a host-key / password prompt (the hang we
#: chased): non-interactive, accept the host key, no stdin.
_PINGGY_SSH_OPTS = (
    "-p 443 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
    "-o BatchMode=yes -o ServerAliveInterval=30 -n"
).split()


def pinggy_spec(port: int) -> TunnelSpec:
    """pinggy over ssh (zero-install): ``ssh -R 0:localhost:<port> free.pinggy.io``."""
    argv = ["ssh", *_PINGGY_SSH_OPTS, "-R", f"0:localhost:{port}", "free.pinggy.io"]
    return TunnelSpec(argv, re.compile(r"https://[\w.-]+\.pinggy[\w.-]*\.link"))


def localtunnel_spec(port: int, runtime: str = "bunx") -> TunnelSpec:
    """localtunnel via the bun (or npx) runtime."""
    argv = [runtime, "localtunnel", "--port", str(port)]
    return TunnelSpec(argv, re.compile(r"https://[\w-]+\.loca\.lt"))


_PROVIDERS = {"pinggy": pinggy_spec, "localtunnel": localtunnel_spec}


@dataclass
class Tunnel:
    """A running tunnel: the public URL and the subprocess forwarding the port."""

    port: int
    url: str
    proc: asyncio.subprocess.Process


class TunnelManager:
    """Owns the per-port tunnel subprocesses and guarantees they're cleaned up.

    One per tool registry (shared by the serve tools). Keyed by local port, so tunnelling
    the same port twice reuses the live tunnel. ``close_all`` (and an atexit hook) kills
    everything.
    """

    def __init__(
        self,
        *,
        provider: str = "pinggy",
        runtime: str = "bunx",
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._provider = provider
        self._runtime = runtime
        self._timeout = timeout_seconds
        self._tunnels: dict[int, Tunnel] = {}
        self._cleanup_registered = False

    def _spec(self, port: int) -> TunnelSpec:
        builder = _PROVIDERS.get(self._provider)
        if builder is None:
            raise TunnelError(
                f"unknown tunnel provider {self._provider!r} (try: {', '.join(_PROVIDERS)})"
            )
        if builder is localtunnel_spec:
            return localtunnel_spec(port, self._runtime)
        return builder(port)

    async def open(self, port: int) -> str:
        """Open (or reuse) a public tunnel to ``port``; return its https URL."""
        live = self._tunnels.get(port)
        if live is not None and live.proc.returncode is None:
            return live.url

        spec = self._spec(port)
        try:
            proc = await asyncio.create_subprocess_exec(
                *spec.argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise TunnelError(
                f"{spec.argv[0]!r} not found — can't open a {self._provider} tunnel"
            ) from exc

        try:
            url = await asyncio.wait_for(self._read_url(proc, spec.url_re), timeout=self._timeout)
        except Exception:  # timeout, EOF, or any read error — never leak a live proc
            await self._kill(proc)
            raise TunnelError(
                f"{self._provider} tunnel produced no URL within {self._timeout:.0f}s"
            ) from None

        self._tunnels[port] = Tunnel(port=port, url=url, proc=proc)
        self._register_cleanup_once()
        return url

    @staticmethod
    async def _read_url(proc: asyncio.subprocess.Process, url_re: re.Pattern[str]) -> str:
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:  # EOF — the process died without printing a URL
                raise TunnelError("tunnel process exited before printing a URL")
            match = url_re.search(raw.decode("utf-8", errors="replace"))
            if match:
                return match.group(0)

    def get(self, port: int) -> Tunnel | None:
        """The live tunnel for ``port``, or ``None``."""
        return self._tunnels.get(port)

    async def close(self, port: int) -> None:
        """Kill the tunnel for ``port`` if there is one."""
        tunnel = self._tunnels.pop(port, None)
        if tunnel is not None:
            await self._kill(tunnel.proc)

    async def _kill(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE)
        except TimeoutError:
            proc.kill()
            await proc.wait()

    def _register_cleanup_once(self) -> None:
        if not self._cleanup_registered:
            atexit.register(self._cleanup_at_exit)
            self._cleanup_registered = True

    def _cleanup_at_exit(self) -> None:  # pragma: no cover - shutdown best-effort
        for tunnel in list(self._tunnels.values()):
            try:
                tunnel.proc.terminate()
            except Exception:
                pass
        self._tunnels.clear()

    async def close_all(self) -> None:
        """Kill every tunnel; called by Gaia.close on the live loop."""
        for port in list(self._tunnels):
            try:
                await self.close(port)
            except Exception:  # pragma: no cover - shutdown best-effort
                pass
