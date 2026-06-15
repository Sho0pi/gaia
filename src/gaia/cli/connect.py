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

import sys
import termios
import tty
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Annotated

import typer

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
    "cli": "terminal chat — always available",
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


def connect(
    ctx: typer.Context,
    connectors: Annotated[
        list[str] | None,
        typer.Argument(help="Connector names (telegram/whatsapp/cli); empty = pick from a menu."),
    ] = None,
    token: Annotated[
        str | None, typer.Option("--token", help="Telegram bot token (skips the prompt).")
    ] = None,
    verify: Annotated[
        bool, typer.Option("--verify/--no-verify", help="Check the telegram token via getMe.")
    ] = True,
    timeout: Annotated[
        int, typer.Option("--timeout", help="Seconds to wait for the WhatsApp QR scan.")
    ] = 120,
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
        selected = _choose(settings)
        if not selected:
            out.print("nothing selected — bye")
            return

    done: list[str] = []
    for name in selected:
        ok = {
            "telegram": lambda: _connect_telegram(settings, token=token, verify=verify),
            "whatsapp": lambda: _connect_whatsapp(settings, timeout=timeout),
            "cli": _connect_cli,
        }[name]()
        if ok:
            done.append(name)

    if done:
        out.print(f"\n[bold green]connected:[/] {', '.join(done)}")
        if set(done) - {"cli"}:
            out.print("run [cyan]gaia start[/] to bring the background connectors up")
    if set(selected) - set(done):
        raise typer.Exit(1)


def _choose(settings: Settings) -> list[str]:
    """Inline multi-select in a TTY; numbered fallback for scripted runs."""
    if sys.stdin.isatty() and sys.stdout.isatty():  # pragma: no cover - real terminal path
        return _choose_interactive(settings)
    return _choose_numbered(settings)


def _choose_numbered(settings: Settings) -> list[str]:
    """Numbered multi-select fallback for tests, pipes, and non-TTY shells."""
    out = console()
    names = list(CONNECTORS)
    out.print("Connectors:")
    for i, name in enumerate(names, 1):
        out.print(f"  {i}. {name}  ({CONNECTORS[name]}) — {_status(settings, name)}")
    raw = typer.prompt("Select (comma-separated numbers, e.g. 1,2)", default="")
    picked = []
    for token_ in raw.split(","):
        token_ = token_.strip()
        if token_.isdigit() and 1 <= int(token_) <= len(names):
            picked.append(names[int(token_) - 1])
    return picked


def _choose_interactive(settings: Settings) -> list[str]:
    names = list(CONNECTORS)
    rows = [(name, CONNECTORS[name], _status(settings, name)) for name in names]
    keys = _tty_keys(sys.stdin.fileno())
    return _run_picker(rows, keys, sys.stdout.write)


def _run_picker(
    rows: list[tuple[str, str, str]], keys: Iterable[str], write: Callable[[str], object]
) -> list[str]:
    """Drive the inline picker. Extracted so key semantics are unit-testable."""
    cursor = 0
    selected: set[int] = set()
    rendered_lines = 0

    def render() -> None:
        nonlocal rendered_lines
        if rendered_lines:
            write("\x1b[2K\r" + "\x1b[1A\x1b[2K\r" * (rendered_lines - 1))
        lines = ["Which connectors?  ↑/↓ move · space select · enter submit · esc cancel"]
        for i, (name, hint, status) in enumerate(rows):
            pointer = ">" if i == cursor else " "
            mark = "◉" if i in selected else "◯"
            lines.append(f"{pointer} {mark} {name:<9} {hint} — {status}")
        text = "\n".join(lines) + "\n"
        write(text)
        rendered_lines = len(lines)

    write("\x1b[?25l")  # hide cursor while moving through the list
    try:
        render()
        for key in keys:
            if key == "up":
                cursor = (cursor - 1) % len(rows)
            elif key == "down":
                cursor = (cursor + 1) % len(rows)
            elif key == "space":
                selected.symmetric_difference_update({cursor})
            elif key == "enter":
                if not selected:
                    selected.add(cursor)
                render()
                return [rows[i][0] for i in range(len(rows)) if i in selected]
            elif key == "esc":
                render()
                return []
            render()
        return []
    finally:
        write("\x1b[?25h")


def _tty_keys(fd: int) -> Iterable[str]:
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch in ("\r", "\n"):
                yield "enter"
            elif ch == " ":
                yield "space"
            elif ch == "\x1b":
                nxt = sys.stdin.read(1)
                if nxt != "[":
                    yield "esc"
                    continue
                code = sys.stdin.read(1)
                if code == "A":
                    yield "up"
                elif code == "B":
                    yield "down"
            elif ch in ("\x03", "\x04"):
                yield "esc"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _status(settings: Settings, name: str) -> str:
    if name == "telegram":
        from gaia import constants

        token = get_env_var(constants.ENV_FILE, "GAIA_TELEGRAM_BOT_TOKEN")
        if token or settings.telegram_bot_token:
            return "configured"
        return "not configured"
    if name == "whatsapp":
        return "configured" if settings.whatsapp_session_db.exists() else "not configured"
    return "built in"


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
    return True


async def _pair(session_db: object, timeout_s: int) -> bool:
    """Run the connector's foreground QR pairing (seam for tests)."""
    from pathlib import Path

    from gaia.connectors import WhatsAppWebConnector

    async def _noop_dispatch(
        _sender_id: str, _name: str, _text: str, _send: object
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
