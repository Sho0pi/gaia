"""``gaia user`` command group — manage the cross-channel user store from the terminal.

Wraps the same ``UserStore`` the connectors + memory key off (``~/.gaia/users.json``). The local
operator owns the machine, so these run unguarded — the terminal is the trust boundary (the chat
``/user`` family is ACL-gated instead). Mirrors that chat family one-for-one.

Lazy-import rule (repo convention): typer + stdlib (+ cli siblings) at module level.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

from gaia.cli import _complete
from gaia.cli._console import console, emit_json
from gaia.cli._options import state

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.users import UserStore

app = typer.Typer(
    name="user", help="Manage known users (roles, names, channels).", no_args_is_help=True
)

RefArg = Annotated[
    str,
    typer.Argument(
        help="A user: canonical id (e.g. 'itay') or 'channel:sender'.",
        autocompletion=_complete.user_refs,
    ),
]
RoleArg = Annotated[
    str, typer.Argument(help="Role: admin | user | guest.", autocompletion=_complete.roles)
]
NameArg = Annotated[str, typer.Argument(help="Display name.")]
IdentityArg = Annotated[str, typer.Argument(help="A 'channel:sender' identity to attach.")]
YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation.")]


def _resolve(store: UserStore, ref: str) -> str | None:
    """Resolve a user ref (canonical id or 'channel:sender') to a canonical user id."""
    if store.get(ref) is not None:
        return ref
    channel, _, sender = ref.partition(":")
    if sender:
        user = store.resolve(channel, sender)
        if user is not None:
            return user.id
    return None


@app.command("list")
def list_users(ctx: typer.Context) -> None:
    """List known users — id, name, role, and the channels that reach them."""
    from gaia.users import UserStore

    users = UserStore().list()
    if state(ctx).json:
        emit_json({"users": [u.model_dump(mode="json") for u in users]})
        return
    out = console()
    if not users:
        out.print("no users yet — they're learned at first contact")
        return
    from rich.table import Table

    table = Table(show_edge=False, pad_edge=False)
    for col in ("id", "name", "role", "channels"):
        table.add_column(col)
    for u in users:
        table.add_row(u.id, u.name or "-", u.role, ", ".join(u.identities) or "-")
    out.print(table)


@app.command()
def show(ctx: typer.Context, ref: RefArg) -> None:
    """Print every field of one user (raw JSON with --json)."""
    from gaia.users import UserStore

    store = UserStore()
    user_id = _resolve(store, ref)
    user = store.get(user_id) if user_id else None
    if user is None:
        console().print(f"no user matching {ref!r}")
        raise typer.Exit(1)
    if state(ctx).json:
        emit_json(user.model_dump(mode="json"))
    else:
        console().print(user.model_dump(mode="json"))


@app.command()
def role(ctx: typer.Context, ref: RefArg, role: RoleArg) -> None:
    """Set a user's role (approve a guest)."""
    from gaia.users import UserStore

    if role not in ("admin", "user", "guest"):
        console().print(f"[red]role must be admin | user | guest, not {role!r}[/]")
        raise typer.Exit(2)
    store = UserStore()
    user_id = _resolve(store, ref)
    updated = store.set_role(user_id, role) if user_id else None  # type: ignore[arg-type]
    if updated is None:
        console().print(f"no user matching {ref!r}")
        raise typer.Exit(1)
    console().print(f"{updated.id} is now {updated.role}")


@app.command()
def name(ctx: typer.Context, ref: RefArg, name: NameArg) -> None:
    """Set a user's display name."""
    from gaia.users import UserStore

    store = UserStore()
    user_id = _resolve(store, ref)
    updated = store.set_name(user_id, name) if user_id else None
    if updated is None:
        console().print(f"no user matching {ref!r}")
        raise typer.Exit(1)
    console().print(f"{updated.id} is now {updated.name!r}")


@app.command()
def link(ctx: typer.Context, ref: RefArg, identity: IdentityArg) -> None:
    """Attach another channel id ('channel:sender') to a user."""
    from gaia.users import UserStore

    channel, _, sender = identity.partition(":")
    if not sender:
        console().print(f"[red]identity must be 'channel:sender', not {identity!r}[/]")
        raise typer.Exit(2)
    store = UserStore()
    user_id = _resolve(store, ref)
    updated = store.link(user_id, channel, sender) if user_id else None
    if updated is None:
        console().print(f"no user matching {ref!r}")
        raise typer.Exit(1)
    console().print(f"{updated.id} now reachable on {', '.join(updated.identities)}")


@app.command("rm")
def remove(ctx: typer.Context, ref: RefArg, yes: YesOpt = False) -> None:
    """Delete a user from the store."""
    from gaia.users import UserStore

    store = UserStore()
    user_id = _resolve(store, ref)
    if user_id is None:
        console().print(f"no user matching {ref!r}")
        raise typer.Exit(1)
    if not yes:
        typer.confirm(f"delete user {user_id!r}?", abort=True)
    store.remove(user_id)
    console().print(f"removed {user_id}")
