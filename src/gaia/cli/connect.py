"""``gaia connect`` — interactive connector setup (openclaw-style onboarding).

Bare invocation opens a Textual checkbox multi-select over the available connectors;
each selected one runs its credential flow: a short tutorial, the token prompt / QR
pairing, an existing-credentials keep-or-replace gate, then the
``connectors.<name>.enabled`` flip in ``gaia.yaml`` (comment-preserving). Secrets land
in ``~/.gaia/.env`` (0600), never in yaml. ``gaia connect telegram`` skips the menu.

Testability (issue #105 rule): every interactive step funnels through the small
``_choose``/prompt helpers — when stdin isn't a tty (CliRunner, pipes) the Textual
picker degrades to a numbered ``typer.prompt``, so all flows run on scripted input.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Annotated, Any

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
        selected = _choose()
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


def _choose() -> list[str]:
    """Checkbox multi-select (Textual); numbered-prompt fallback off-tty."""
    if sys.stdin.isatty():  # pragma: no cover - real-terminal path, exercised manually
        return _choose_textual()

    out = console()
    names = list(CONNECTORS)
    out.print("Connectors:")
    for i, name in enumerate(names, 1):
        out.print(f"  {i}. {name}  ({CONNECTORS[name]})")
    raw = typer.prompt("Select (comma-separated numbers, e.g. 1,2)", default="")
    picked = []
    for token_ in raw.split(","):
        token_ = token_.strip()
        if token_.isdigit() and 1 <= int(token_) <= len(names):
            picked.append(names[int(token_) - 1])
    return picked


def _choose_textual() -> list[str]:  # pragma: no cover - thin .run() wrapper, see build_picker
    """Run the Textual picker; ``[]`` if the user quits with Ctrl-C without confirming."""
    return build_picker().run() or []


def build_picker() -> Any:
    """The Textual connector picker app: space toggles a connector, Connect submits.

    Returns an ``App[list[str]]`` whose ``return_value`` is the selected names. Split out
    of :func:`_choose_textual` so it can be driven headlessly (Textual ``run_test``)
    without a real terminal. Textual is imported lazily so the command tree stays light.
    """
    from typing import ClassVar

    from textual.app import App, ComposeResult
    from textual.binding import BindingType
    from textual.containers import Center
    from textual.widgets import Button, Footer, Header, Label, SelectionList
    from textual.widgets.selection_list import Selection

    class _Picker(App[list[str]]):
        TITLE = "gaia connect"
        CSS = """
        SelectionList { height: auto; margin: 1 2; border: round $primary; padding: 1 2; }
        Label { margin: 1 2 0 2; }
        #go { margin: 1 2; }
        """
        BINDINGS: ClassVar[list[BindingType]] = [("ctrl+c", "quit", "Cancel")]

        def compose(self) -> ComposeResult:
            yield Header()
            yield Label("Which connectors do you want to set up? [dim](space toggles)[/]")
            yield SelectionList[str](
                *(Selection(f"{name}  ({hint})", name) for name, hint in CONNECTORS.items())
            )
            yield Center(Button("Connect", variant="primary", id="go"))
            yield Footer()

        def on_mount(self) -> None:
            self.query_one(SelectionList).focus()

        def on_button_pressed(self, _event: Button.Pressed) -> None:
            self.exit(list(self.query_one(SelectionList).selected))

    return _Picker()


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
    out.print(
        "telegram connected. Note: the chat TUI is foreground-exclusive — background "
        "connectors run via [cyan]gaia start[/], not alongside [cyan]gaia[/]."
    )
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

    async def _noop_handler(_text: str, _send: object) -> None:  # pragma: no cover
        return None

    connector = WhatsAppWebConnector(Path(str(session_db)), _noop_handler)
    return await connector.pair(timeout_s=timeout_s)


# --- cli ----------------------------------------------------------------------------


def _connect_cli() -> bool:
    console().print("cli is always available — just run [cyan]gaia[/] to chat. Nothing to set up.")
    return True
