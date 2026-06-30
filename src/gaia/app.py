"""Application launcher: build Gaia, read gaia.yaml, launch the enabled connectors.

Which connectors run is driven by ``gaia.yaml`` (:class:`~gaia.config.GaiaConfig`),
not by which credentials happen to be present. The WhatsApp *backend* (business vs
regular/QR) is still auto-picked from creds by :func:`select_connector`.

Launch rules (:func:`plan_launch`, pure + unit-testable):

* The CLI chat is a **foreground** REPL — it cannot share the event loop with
  background connectors, so enabling it alongside another connector is rejected.
* The remaining connectors are async and co-run via :func:`asyncio.gather`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
from pathlib import Path
from typing import Any

from gaia import constants
from gaia.config import (
    BACKGROUND_CONNECTORS,
    GaiaConfig,
    Settings,
    get_settings,
    write_default_config,
)
from gaia.connectors import (
    CLIConnector,
    TelegramConnector,
    WhatsAppConnector,
    WhatsAppWebConnector,
)
from gaia.connectors.base import Dispatch
from gaia.connectors.socket import DaemonNotRunningError, SocketChatClient, SocketConnector
from gaia.core import Gaia
from gaia.core.dispatch import build_dispatcher
from gaia.logs import setup_logging

logger = logging.getLogger(__name__)


def select_connector(
    settings: Settings,
    dispatch: Dispatch,
    *,
    transcriber: Any = None,
    group_trigger: Any = None,
    show_active: bool = True,
) -> WhatsAppConnector | WhatsAppWebConnector:
    """Choose the WhatsApp backend from configured credentials.

    ``dispatch`` is the whatsapp-channel-bound dispatch callable; ``transcriber``
    (``gaia.voice.Transcriber`` or None) turns inbound voice notes into text on the web
    backend; the business backend has no voice path yet (webhook, #3). ``group_trigger``
    (``gaia.config.GroupTrigger`` or None) drives the group-chat gating; ``show_active``
    drives the web backend's blue-tick + "typing…" presence.
    """
    if settings.has_whatsapp_business:
        assert settings.whatsapp_phone_id and settings.whatsapp_token  # narrowed by property
        return WhatsAppConnector(settings.whatsapp_phone_id, settings.whatsapp_token, dispatch)
    return WhatsAppWebConnector(
        settings.whatsapp_session_db,
        dispatch,
        transcriber=transcriber,
        group_trigger=group_trigger,
        show_active=show_active,
    )


def plan_launch(config: GaiaConfig, *, daemon: bool = False) -> list[str]:
    """Return the names of connectors to launch, or raise on an invalid combo.

    Pure: no I/O, so the policy (CLI-exclusivity, enabled set) is unit-testable.
    ``daemon=True`` is the background-service mode: the cli connector is foreground-only
    and silently excluded, so the default config works for both ``gaia chat`` and
    ``gaia start`` without yaml surgery.
    """
    connectors = config.connectors
    background = [name for name in BACKGROUND_CONNECTORS if getattr(connectors, name).enabled]
    if daemon:
        return background
    if connectors.cli.enabled:
        if background:
            raise ValueError(
                "The CLI connector is foreground-exclusive and cannot run alongside "
                f"other connectors (also enabled: {', '.join(background)}). "
                "Disable the others or disable cli in gaia.yaml."
            )
        return ["cli"]
    return background


def run_cli(settings: Settings | None = None, *, env_file: Path | None = None) -> None:
    """Launch the local inline CLI client and attach to the running daemon."""
    if settings is None:
        get_settings(env_file)  # preserve env-file loading/validation side effects
    _run_tui(constants.SOCKET_FILE)


def _run_tui(socket_path: Path) -> None:
    """Run the inline chat as a client of the daemon socket."""

    async def _main() -> None:
        client = SocketChatClient(socket_path)
        try:
            await client.ensure_available()
        except DaemonNotRunningError as exc:
            from gaia.cli._console import console

            console().print(str(exc))
            raise SystemExit(3) from exc
        await CLIConnector(client.dispatch).run_async()

    asyncio.run(_main())


def run_dev(
    settings: Settings | None = None,
    *,
    env_file: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Launch ADK's dev web UI on Gaia — inspect tool calls and LLM requests live."""
    from gaia.dev import serve_dev

    settings = settings or get_settings(env_file)
    write_default_config(settings.config_path)  # fresh home: drop the commented default (#55)
    gaia = Gaia(settings)
    setup_logging(settings, gaia.config.logging)
    serve_dev(gaia, host=host, port=port)


def run_auth(provider: str, *, env_file: Path | None = None) -> None:
    """Run an interactive provider login and store the credentials.

    Currently supports ``openai`` (Sign in with ChatGPT, device-code flow).
    """
    settings = get_settings(env_file)
    # Logging config only — don't build a whole Gaia (tool registry, souls, container) just
    # to log in. Read the live config directly.
    from gaia.config import ConfigSupplier

    setup_logging(settings, ConfigSupplier(settings.config_path).current.logging)
    if provider in ("openai", "openai-chatgpt", "chatgpt"):
        from gaia.providers.openai import login

        creds = asyncio.run(login())
        creds.save()
        logger.info("ChatGPT credentials saved for account %s", creds.account_id)
    else:
        raise SystemExit(f"unknown auth provider: {provider!r} (try: openai)")


def run(settings: Settings | None = None, *, env_file: Path | None = None) -> None:
    """Build Gaia and launch the connectors enabled in gaia.yaml."""
    settings = settings or get_settings(env_file)
    write_default_config(settings.config_path)
    gaia = Gaia(settings)
    selected = plan_launch(gaia.config)
    # The CLI chat owns the terminal prompt, so console log handlers would draw over it.
    setup_logging(settings, gaia.config.logging, console=selected != ["cli"])

    if not selected:
        logger.warning("no connectors enabled in gaia.yaml — nothing to run")
        return

    if selected == ["cli"]:
        _run_tui(constants.SOCKET_FILE)
        return

    asyncio.run(_run_background(settings, gaia, selected))


def run_daemon(
    settings: Settings | None = None, *, env_file: Path | None = None, hold: bool = False
) -> int:
    """Foreground daemon runner (``gaia serve``): background connectors only.

    Daemon planning mode excludes the cli connector (it owns a terminal a daemon does
    not have). Writes the pidfile once startup is committed — its appearance is the
    "made it to the run loop" signal ``gaia start`` polls for — and removes it on
    exit. SIGTERM and SIGINT both take the graceful path (memory flush +
    ``gaia.close()``). Returns the process exit code instead of raising, so the CLI
    maps it onto ``typer.Exit``. ``hold=True`` keeps the loop open with zero
    connectors (tests, service debugging, the future socket gateway).
    """
    from gaia.cli._pidfile import PidFile  # lazy: no module-level app -> cli edge

    settings = settings or get_settings(env_file)
    write_default_config(settings.config_path)
    gaia = Gaia(settings)
    selected = plan_launch(gaia.config, daemon=True)
    # Console handlers stay on: when spawned by `gaia start`, stdout IS daemon.log
    # (the color check is isatty-gated, so no ANSI lands in the file).
    setup_logging(settings, gaia.config.logging)
    if not selected:
        logger.info("no background channels enabled — daemon will serve local CLI socket only")
    pidfile = PidFile()
    pidfile.write()
    try:
        _run_until_complete(_serve(settings, gaia, selected, hold=hold))
    except Exception as exc:
        # A fatal crash (not the graceful SIGTERM/SIGINT cancel): capture a redacted report and
        # log it as an event so the self-monitor sees it, then re-raise → non-zero exit → the
        # service (gaia service) restarts us, and `gaia report` can file it.
        from gaia.crash import write_crash_report
        from gaia.logs import log_error

        path = write_crash_report(exc, settings=settings, context={"connectors": selected})
        log_error("daemon_crash", exc, crash_report=str(path))
        logger.critical("daemon crashed — report at %s", path)
        raise
    finally:
        pidfile.remove()
    return 0


def _run_until_complete(coro: Any) -> None:
    """Run ``coro`` on a fresh loop — like ``asyncio.run`` but WITHOUT joining the default executor.

    ``asyncio.run`` ends with ``loop.shutdown_default_executor()``, which joins every
    ``asyncio.to_thread`` worker with no timeout (Python 3.11). neonize runs its blocking Go/cgo
    calls via ``to_thread`` on that default executor (``neonize/aioze/client.py``); on Linux/arm64
    the whatsmeow ``Stop`` doesn't reliably unblock the long-lived ``Neonize()`` call, so that join
    hangs forever (mac unblocks it → fast). We cancel pending tasks + close the loop but skip the
    executor join; the orphaned worker is reaped by ``gaia serve``'s ``os._exit`` (#300). The
    watchdog stays as the last-resort backstop.
    """
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(coro)
    finally:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:  # pragma: no cover - best-effort
            logger.debug("asyncgen shutdown failed", exc_info=True)
        asyncio.set_event_loop(None)
        loop.close()  # NB: does NOT join the default executor — that's the whole point


#: How long after a stop signal the daemon force-exits if it hasn't terminated. The sole cap on
#: shutdown now — long enough for a real memory flush (Gemini on a Pi ~4-12s) to finish, while still
#: rescuing a genuinely wedged dependency thread.
SHUTDOWN_GRACE_SECONDS = 20.0


def _arm_shutdown_watchdog(grace: float = SHUTDOWN_GRACE_SECONDS) -> threading.Timer:
    """Force-exit ``grace`` seconds after shutdown starts — the daemon must always stop (#297).

    A dependency can leave a wedged **non-daemon** thread that hangs Python's interpreter-exit join
    forever: e.g. mem0's ``ThreadPoolExecutor`` stuck mid-Gemini-call during the final memory flush,
    or neonize/grpc. We can't make their threads daemon or interrupt their network call, so a daemon
    timer that ``os._exit()``s guarantees ``gaia serve`` / ``gaia stop`` terminates. Daemon → it's
    abandoned for free when we exit cleanly first (the common case, a few seconds). By then
    ``Gaia.close()`` has already torn down our subprocesses on the live loop, so nothing orphans.
    """

    def _force() -> None:  # pragma: no cover - exits the process
        os._exit(0)

    timer = threading.Timer(grace, _force)
    timer.daemon = True
    timer.name = "gaia-shutdown-watchdog"
    timer.start()
    return timer


async def _serve(settings: Settings, gaia: Gaia, selected: list[str], *, hold: bool) -> None:
    """Run connectors until SIGTERM/SIGINT, then exit through the graceful path.

    ``asyncio.run`` only converts SIGINT into KeyboardInterrupt; a plain SIGTERM would
    kill the process without ``_run_background``'s finally (memory flush +
    ``gaia.close()``). Installing loop signal handlers that cancel this task routes both
    signals through that cleanup; our own ``CancelledError`` is swallowed (and
    ``uncancel()``-ed) so ``asyncio.run`` returns cleanly.
    """
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None  # always inside asyncio.run

    stopping = False

    def _request_stop(sig: signal.Signals) -> None:
        nonlocal stopping
        logger.info("received %s — shutting down", sig.name)
        if not stopping:  # arm the watchdog once, on the first stop signal
            stopping = True
            _arm_shutdown_watchdog()
        task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _request_stop, sig)

    def _on_loop_error(_loop: Any, ctx: dict[str, Any]) -> None:
        # Unhandled exceptions in fire-and-forget tasks (a connector, the cron runner) would
        # otherwise vanish — record them so the monitor sees connector faults that don't kill us.
        exc = ctx.get("exception")
        if isinstance(exc, Exception):
            from gaia.logs import log_error

            log_error("daemon_task", exc, detail=ctx.get("message", ""))
        else:
            loop.default_exception_handler(ctx)

    loop.set_exception_handler(_on_loop_error)
    try:
        if hold:
            logger.info("holding daemon open for local socket clients")
        await _run_background(settings, gaia, selected)
    except asyncio.CancelledError:
        task.uncancel()  # our own cancel: swallow it so asyncio.run() returns cleanly
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


async def _run_background(settings: Settings, gaia: Gaia, selected: list[str]) -> None:
    """Run the enabled async connectors (and the cron scheduler) until interrupted.

    ``async with gaia`` scopes the async resources to this coroutine: they are closed
    on the still-running loop on every exit path — clean return, exception, or the
    shutdown cancel (the re-raised ``CancelledError`` still runs ``__aexit__``).
    """
    async with gaia:
        dispatcher = build_dispatcher(gaia)
        tasks: list[asyncio.Task[None]] = []
        socket = SocketConnector(constants.SOCKET_FILE, dispatcher.for_channel(CLIConnector.NAME))
        tasks.append(asyncio.create_task(socket.start()))
        # The container's connectors registry (the same dict the cron runner @inject's and
        # the message_user tool reads). Populate it in place — don't rebind — so both stay
        # pointed at the live senders.
        running: dict[str, Any] = gaia.connectors
        running.clear()
        running[SocketConnector.NAME] = socket

        if "whatsapp" in selected:
            wa_cfg = gaia.config.connectors.whatsapp
            connector = select_connector(
                settings,
                dispatcher.for_channel(WhatsAppWebConnector.NAME),
                transcriber=gaia.container.transcriber(),
                group_trigger=wa_cfg.group_trigger,
                show_active=wa_cfg.show_active,
            )
            if isinstance(connector, WhatsAppWebConnector):
                tasks.append(asyncio.create_task(connector.start()))
                running[WhatsAppWebConnector.NAME] = connector
            else:
                # Business backend delivers inbound over an HTTP webhook that isn't wired
                # yet (issue #3). Build the client so config is validated, but say so loudly.
                connector.build_client()
                logger.warning(
                    "whatsapp business backend selected, but the inbound webhook server is "
                    "not wired yet (see issue #3) — no messages will be received"
                )

        if "telegram" in selected:
            # The token lives in env (GAIA_TELEGRAM_BOT_TOKEN → settings), never in gaia.yaml; the
            # yaml field is only an optional override, so fall back to the env value (the real one).
            token = gaia.config.connectors.telegram.token or settings.telegram_bot_token
            if not token:
                logger.warning(
                    "telegram enabled but no token (set GAIA_TELEGRAM_BOT_TOKEN) — skipping"
                )
            else:
                from gaia.commands import default_registry

                # Flatten the command registry to (name, summary) so the connector can register
                # them with Telegram (setMyCommands) without importing gaia.commands itself.
                cmd_meta = [(c.name, c.summary) for c in default_registry(gaia.config).all()]
                telegram = TelegramConnector(
                    token, dispatcher.for_channel(TelegramConnector.NAME), commands=cmd_meta
                )
                tasks.append(asyncio.create_task(telegram.start()))
                running[TelegramConnector.NAME] = telegram

        # If we just restarted after a crash, let the admin know once (best-effort, non-blocking).
        notice = asyncio.create_task(_notify_recent_crashes(gaia))
        notice.add_done_callback(lambda t: t.exception())  # swallow; never warns

        # Digest conversations that idled out while gaia was off (best-effort, non-blocking).
        sweep = asyncio.create_task(_consolidate_idle_sessions(gaia))
        sweep.add_done_callback(lambda t: t.exception())

        scheduler = _start_cron(gaia)
        mission_dispatcher = _start_dispatcher(gaia)
        improve_scheduler = _start_improve(gaia)
        monitor_scheduler = _start_monitor(gaia)
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            # On shutdown the cancel hits us mid-``gather``; ``gather`` schedules the
            # children's cancellation but does NOT wait for them, so re-cancel and await so
            # each connector runs its own teardown (whatsapp stop, telegram stop) first.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            if scheduler is not None:
                scheduler.shutdown()
            if improve_scheduler is not None:
                improve_scheduler.shutdown()
            if monitor_scheduler is not None:
                monitor_scheduler.shutdown()
            if mission_dispatcher is not None:
                await mission_dispatcher.stop()
            # No memory flush on shutdown: the conversation lives in the durable session, so a stop
            # leaves it to be consolidated on the next idle (or the startup sweep). Fast, simple, no
            # data loss (#76, replaces #300/#307).


async def _notify_recent_crashes(gaia: Gaia) -> None:
    """If we restarted after fresh crashes, DM the admin once — 'crashed, run `gaia report`'."""
    from gaia.crash import last_reported, mark_reported, recent_crashes

    crashes = recent_crashes(since=last_reported())
    if not crashes:
        return
    from gaia.tools.message import user_address

    n = len(crashes)
    text = (
        f"⚠ gaia crashed {n} time{'s' if n != 1 else ''} and restarted. "
        "Run `gaia report` to file it (crash logs are in ~/.gaia/crashes)."
    )
    for admin in (u for u in gaia.users.list() if u.role == "admin" and u.identities):
        addr = user_address(gaia.users, admin.id)
        if addr is None:
            continue
        channel, chat = addr
        sender = gaia.connectors.get(channel)
        if sender is not None:
            try:
                await sender.send_to(chat, text)
            except Exception:  # best-effort — a down connector never blocks startup
                logger.debug("crash notice to %s failed", channel, exc_info=True)
    mark_reported()


async def _consolidate_idle_sessions(gaia: Gaia) -> None:
    """Startup sweep: digest + clear conversations that idled out while gaia was off (#76).

    Idle consolidation only runs while the daemon is up, so a conversation that "ended" before a
    stop/reboot would otherwise linger un-digested. We scan each user's durable sessions and, for
    any whose last activity is older than the idle threshold, distil it into memory + delete it.
    Best-effort: a mem0 hiccup is logged and skipped.
    """
    import time

    mem = gaia.memory_service
    if mem is None or not gaia.config.memory.auto_ingest:
        return
    svc = gaia.session_service
    cutoff = gaia.config.sessions.idle_consolidate_minutes * 60.0
    now = time.time()
    for user in gaia.users.list():
        resp = await svc.list_sessions(app_name=constants.APP_NAME, user_id=user.id)
        for meta in getattr(resp, "sessions", []):
            if now - (meta.last_update_time or 0) < cutoff:
                continue  # still "active" — leave it for the live idle timer
            session = await svc.get_session(
                app_name=constants.APP_NAME, user_id=user.id, session_id=meta.id
            )
            if session is None or not session.events:
                continue
            try:
                await mem.add_session_to_memory(session)
            except Exception:
                logger.warning("startup consolidation failed for %s", meta.id)
            await svc.delete_session(
                app_name=constants.APP_NAME, user_id=user.id, session_id=meta.id
            )


def _start_improve(gaia: Gaia) -> Any:
    """Start the self-improve scheduler for the daemon (None when disabled)."""
    if not gaia.config.analysis.enabled:
        return None
    from gaia.analysis.loop import run_cycle
    from gaia.analysis.scheduler import AnalysisScheduler

    scheduler = AnalysisScheduler(
        lambda: run_cycle(gaia), interval_hours=gaia.config.analysis.interval_hours
    )
    scheduler.start()
    return scheduler


def _start_monitor(gaia: Gaia) -> Any:
    """Start the self-monitoring scheduler for the daemon (None when disabled)."""
    if not gaia.config.monitor.enabled:
        return None
    from gaia.analysis.scheduler import AnalysisScheduler
    from gaia.monitor.loop import run_cycle

    scheduler = AnalysisScheduler(
        lambda: run_cycle(gaia), interval_hours=gaia.config.monitor.interval_hours, name="monitor"
    )
    scheduler.start()
    return scheduler


def _start_cron(gaia: Gaia) -> Any:
    """Start the cron scheduler for the daemon (None when disabled)."""
    if not gaia.config.cron.enabled:
        return None
    from gaia.cron import CronScheduler, CronStore
    from gaia.cron.runner import make_runner

    scheduler = CronScheduler(CronStore(), make_runner(gaia))
    scheduler.start()
    return scheduler


def _start_dispatcher(gaia: Gaia) -> Any:
    """Start the mission dispatcher for the daemon (None when disabled)."""
    cfg = gaia.config.missions
    if not cfg.enabled:
        return None
    from gaia.missions.dispatcher import MissionDispatcher

    dispatcher = MissionDispatcher(
        gaia, max_concurrent=cfg.max_concurrent, poll_seconds=cfg.poll_seconds
    )
    dispatcher.start()
    return dispatcher


def send_message(
    channel: str,
    sender_id: str,
    text: str,
    *,
    name: str = "",
    settings: Settings | None = None,
    env_file: Path | None = None,
) -> list[str]:
    """Drive one inbound message through the full dispatch path; return the replies as text.

    Same path a live connector takes: resolve ``channel:sender_id`` → user, gate guests,
    route to the per-user handler. Powers ``gaia msg`` as a sanity check for the
    multi-user access gate without a live connector. An **empty list** means the sender
    was gated (an unknown sender on a guest-default channel, or an explicit guest) — no
    reply was emitted; a non-empty list is a real model reply for a known user/admin.
    """
    from gaia.connectors.base import Inbound, Reply, as_text
    from gaia.core.dispatch import build_dispatcher

    settings = settings or get_settings(env_file)
    gaia = Gaia(settings)
    setup_logging(settings, gaia.config.logging, console=False)

    async def _run() -> list[str]:
        replies: list[str] = []

        async def send(reply: Reply) -> None:
            replies.append(as_text(reply))

        async with gaia:
            dispatch = build_dispatcher(gaia).for_channel(channel)
            await dispatch(sender_id, name, Inbound(text=text), send)
        return replies

    return asyncio.run(_run())
