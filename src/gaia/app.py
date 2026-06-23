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
import signal
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
        asyncio.run(_serve(settings, gaia, selected, hold=hold))
    finally:
        pidfile.remove()
    return 0


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

    def _request_stop(sig: signal.Signals) -> None:
        logger.info("received %s — shutting down", sig.name)
        task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _request_stop, sig)
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
            token = gaia.config.connectors.telegram.token
            if not token:
                logger.warning(
                    "telegram enabled but no token (set GAIA_TELEGRAM_BOT_TOKEN) — skipping"
                )
            else:
                telegram = TelegramConnector(token, dispatcher.for_channel(TelegramConnector.NAME))
                tasks.append(asyncio.create_task(telegram.start()))
                running[TelegramConnector.NAME] = telegram

        scheduler = _start_cron(gaia)
        mission_dispatcher = _start_dispatcher(gaia)
        improve_scheduler = _start_improve(gaia)
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
            if mission_dispatcher is not None:
                await mission_dispatcher.stop()
            # Drain any turns still buffered for memory before the process exits, so a
            # Ctrl-C doesn't drop the tail of the conversation (best-effort).
            await dispatcher.flush_all()


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
