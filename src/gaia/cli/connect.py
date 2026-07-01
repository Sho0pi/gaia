"""``gaia connect`` — interactive connector setup (openclaw-style onboarding).

Bare invocation opens an inline interactive multi-select over the available connectors;
each selected one runs its credential flow: a short tutorial, the token prompt / QR
pairing, an existing-credentials keep-or-replace gate, then the
``connectors.<name>.enabled`` flip in ``gaia.yaml`` (comment-preserving). Secrets land
in ``~/.gaia/.env`` (0600), never in yaml. ``gaia connect telegram`` skips the menu.

Testability (issue #105 rule): every interactive step funnels through small helpers;
non-TTY runs use a numbered ``typer.prompt``, so flows run on scripted input.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

import typer

from gaia.cli import _complete
from gaia.cli._console import console
from gaia.cli._envfile import get_env_var, set_env_var
from gaia.cli._options import state
from gaia.cli._yamledit import set_config_value

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.config import Settings

#: name -> one-line menu hint, in display order.
CONNECTORS: dict[str, str] = {
    "telegram": "bot token from @BotFather",
    "whatsapp": "pair your personal account by QR",
}

_TELEGRAM_TUTORIAL = """\
[bold]Telegram setup[/]
  1. Open Telegram and message [cyan]@BotFather[/].
  2. Send [cyan]/newbot[/], pick a display name and a unique @username.
  3. BotFather replies with a token like [dim]123456789:AAF...xyz[/] — paste it below.
"""

_WHATSAPP_TUTORIAL = """\
[bold]WhatsApp pairing[/] (personal account, WhatsApp-Web protocol)
  1. On your phone open WhatsApp → Settings → [cyan]Linked Devices[/].
  2. Tap [cyan]Link a Device[/].
  3. Scan the QR code that appears below. The session is saved locally, so this
     is a one-time step.
"""


ConnectorsArg = Annotated[
    list[str] | None,
    typer.Argument(
        help="Connector names (telegram/whatsapp); empty = pick from a menu.",
        autocompletion=_complete.channels,
    ),
]
TokenOpt = Annotated[
    str | None, typer.Option("--token", help="Telegram bot token (skips the prompt).")
]
VerifyOpt = Annotated[
    bool, typer.Option("--verify/--no-verify", help="Check the telegram token via getMe.")
]
TimeoutOpt = Annotated[
    int, typer.Option("--timeout", help="Seconds to wait for the WhatsApp QR scan.")
]


def connect(
    ctx: typer.Context,
    connectors: ConnectorsArg = None,
    token: TokenOpt = None,
    verify: VerifyOpt = True,
    timeout: TimeoutOpt = 120,
) -> None:
    """Set up chat connectors: pick them from a menu, then connect each one."""
    from gaia.config import get_settings

    settings = get_settings(state(ctx).env_file)
    out = console()

    selected = list(connectors or [])
    for name in selected:
        if name not in CONNECTORS:
            out.print(f"unknown connector {name!r} — choose from: {', '.join(CONNECTORS)}")
            raise typer.Exit(2)
    if not selected:
        selected, to_remove = _choose(settings)
        for name in to_remove:
            _remove_connector(settings, name)
        if to_remove:
            out.print(f"[yellow]removed:[/] {', '.join(to_remove)}")
        if not selected:
            if not to_remove:
                out.print("nothing selected — bye")
            return

    done: list[str] = []
    for name in selected:
        ok = {
            "telegram": lambda: _connect_telegram(settings, token=token, verify=verify),
            "whatsapp": lambda: _connect_whatsapp(settings, timeout=timeout),
        }[name]()
        if ok:
            done.append(name)

    if done:
        out.print(f"\n[bold green]connected:[/] {', '.join(done)}")
        out.print("run [cyan]gaia start[/] to bring the background connectors up")
    if set(selected) - set(done):
        raise typer.Exit(1)


def _choose(settings: Settings) -> tuple[list[str], list[str]]:
    """Grouped picker (gaia style): returns (to_set_up, to_remove). Configured connectors show `―`;
    space ticks `●` to set up, backspace marks a configured one `✗` for removal."""
    from gaia.cli._select import select_manage

    options = [(name, name, hint) for name, hint in CONNECTORS.items()]
    marked = [name for name in CONNECTORS if _configured(settings, name)]
    return select_manage("Connectors", options, marked=marked)


def _configured(settings: Settings, name: str) -> bool:
    from gaia import constants

    if name == "telegram":
        return bool(
            get_env_var(constants.ENV_FILE, "GAIA_TELEGRAM_BOT_TOKEN")
            or settings.telegram_bot_token
        )
    if name == "whatsapp":
        return settings.whatsapp_session_db.exists()
    return False


def _remove_connector(settings: Settings, name: str) -> None:
    """Disconnect a connector: drop its credentials and disable it in gaia.yaml."""
    from gaia import constants
    from gaia.cli._envfile import unset_env_var

    if name == "telegram":
        unset_env_var(constants.ENV_FILE, "GAIA_TELEGRAM_BOT_TOKEN")
    elif name == "whatsapp":
        settings.whatsapp_session_db.unlink(missing_ok=True)
    set_config_value(settings.config_path, f"connectors.{name}.enabled", False)


# --- telegram -----------------------------------------------------------------------


def _connect_telegram(settings: Settings, *, token: str | None, verify: bool) -> bool:
    from gaia import constants

    out = console()
    out.print(_TELEGRAM_TUTORIAL)

    env_path = constants.ENV_FILE
    existing = get_env_var(env_path, "GAIA_TELEGRAM_BOT_TOKEN") or settings.telegram_bot_token
    if existing and token is None:
        if not typer.confirm("a telegram token is already configured — replace it?"):
            set_config_value(settings.config_path, "connectors.telegram.enabled", True)
            out.print("kept the existing token; telegram enabled")
            return True

    value = token or typer.prompt("Bot token", hide_input=True).strip()
    if not value:
        out.print("[yellow]no token given — skipping telegram[/]")
        return False

    if verify:
        name = _verify_telegram(value)
        if name is None:
            out.print("[red]token rejected by the Telegram Bot API — not saved[/]")
            return False
        out.print(f"token OK — bot [bold]@{name}[/]")

    set_env_var(env_path, "GAIA_TELEGRAM_BOT_TOKEN", value)
    set_config_value(settings.config_path, "connectors.telegram.enabled", True)
    out.print("telegram connected — run [cyan]gaia start[/] to receive messages")
    return True


def _verify_telegram(token: str) -> str | None:
    """The bot's username for a valid token, ``None`` when rejected/unreachable."""
    try:
        import httpx

        resp = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10.0)
        data = resp.json()
        if resp.status_code == 200 and data.get("ok"):
            return str(data["result"]["username"])
        return None
    except Exception:  # network down ≠ bad token: warn and accept unverified
        console().print("[yellow]could not reach the Telegram API — saving unverified[/]")
        return ""


# --- whatsapp -----------------------------------------------------------------------


def _connect_whatsapp(settings: Settings, *, timeout: int) -> bool:
    import asyncio

    out = console()
    out.print(_WHATSAPP_TUTORIAL)

    session_db = settings.whatsapp_session_db
    if session_db.exists():
        if not typer.confirm("a WhatsApp session already exists — re-pair (deletes it)?"):
            set_config_value(settings.config_path, "connectors.whatsapp.enabled", True)
            out.print("kept the existing session; whatsapp enabled")
            return True
        session_db.unlink()

    out.print("starting the pairing client — the QR appears below…\n")
    paired = asyncio.run(_pair(session_db, timeout))
    if not paired:
        out.print(
            f"[red]not paired within {timeout}s[/] — run [cyan]gaia connect whatsapp[/] again"
        )
        return False

    set_config_value(settings.config_path, "connectors.whatsapp.enabled", True)
    out.print("whatsapp paired — the session is saved, no QR needed next time")
    # The QR links gaia's OWN WhatsApp account (the bot). YOU become admin automatically when you
    # send gaia its first message (first-contact bootstrap). Optionally pre-allow other people now.
    out.print("[dim]you'll be admin once you message gaia from your phone.[/]")
    _prompt_allowed_users(out)
    return True


def _prompt_allowed_users(out: Any) -> None:
    """Pre-allow other people as (non-admin) users; seed them past the guest gate."""
    from gaia import constants
    from gaia.users import UserStore, normalize_wa_number

    raw = typer.prompt(
        "Pre-allow other numbers as users (any format — '+972 50-123-4567', '972501234567', "
        "spaces/dashes fine; comma-separated; blank = none)",
        default="",
        show_default=False,
    ).strip()
    if not raw:
        return
    store = UserStore(constants.USERS_FILE)
    added = 0
    for token in raw.split(","):
        jid = normalize_wa_number(token)
        if jid is None:
            continue
        if store.resolve("whatsapp", jid) is None:
            store.register("whatsapp", jid, name=jid.split("@")[0], role="user")
            added += 1
    if added:
        out.print(f"allowed [bold]{added}[/] number(s) as users.")


async def _pair(session_db: object, timeout_s: int) -> bool:
    """Run the connector's foreground QR pairing; True once paired (seam for tests)."""
    from pathlib import Path

    from gaia.connectors import WhatsAppWebConnector
    from gaia.connectors.base import Inbound

    async def _noop_dispatch(
        _sender_id: str, _name: str, _inbound: Inbound, _send: object
    ) -> None:  # pragma: no cover
        return None

    connector = WhatsAppWebConnector(Path(str(session_db)), _noop_dispatch)
    return await connector.pair(timeout_s=timeout_s)


# --- cli ----------------------------------------------------------------------------


def _connect_cli() -> bool:
    console().print(
        "cli chat is built in and always available — run [cyan]gaia start[/], "
        "then [cyan]gaia[/] to chat."
    )
    return True
