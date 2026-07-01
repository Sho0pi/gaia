"""``gaia acl`` command group — inspect & manage access-control capabilities from the terminal.

Wraps the same capability model the chat ``/acl`` family uses: :mod:`gaia.acl` (groups + pure
resolution) over per-user ``grants``/``denies`` on the ``UserStore``. Local operator is trusted,
so unguarded here.

Lazy-import rule (repo convention): typer + stdlib (+ cli siblings) at module level.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

from gaia.cli import _complete
from gaia.cli._console import console, emit_json
from gaia.cli._options import state
from gaia.commands.catalog import summary_of

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.users import UserStore

app = typer.Typer(name="acl", help="Inspect and manage ACL capabilities.", no_args_is_help=True)

RefArg = Annotated[
    str,
    typer.Argument(
        help="A user: canonical id (e.g. 'itay') or 'channel:sender'.",
        autocompletion=_complete.user_refs,
    ),
]
CapArg = Annotated[
    str,
    typer.Argument(
        help="A capability: a group (e.g. 'web', 'shell'), a tool id, or '*'.",
        autocompletion=_complete.capabilities,
    ),
]


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


@app.command("list", help=summary_of("acl"))
def list_groups(ctx: typer.Context) -> None:
    # help text comes from the shared command catalog (same wording as the chat `/acl`).
    from gaia.acl import GROUPS

    groups = {name: sorted(tools) for name, tools in GROUPS.items()}
    if state(ctx).json:
        emit_json({"groups": groups})
        return
    from rich.table import Table

    table = Table(show_edge=False, pad_edge=False)
    table.add_column("capability")
    table.add_column("grants")
    for name, tools in groups.items():
        table.add_row(name, ", ".join(tools))
    console().print(table)


@app.command()
def perms(ctx: typer.Context, ref: RefArg) -> None:
    """Show a user's effective capabilities (role defaults + grants, minus denies)."""
    from gaia.acl import effective_capabilities
    from gaia.config import ConfigSupplier, get_settings
    from gaia.users import UserStore

    store = UserStore()
    user_id = _resolve(store, ref)
    user = store.get(user_id) if user_id else None
    if user is None:
        console().print(f"no user matching {ref!r}")
        raise typer.Exit(1)
    config = ConfigSupplier(get_settings(state(ctx).env_file).config_path).current
    caps = sorted(effective_capabilities(user, config))
    if state(ctx).json:
        emit_json({"user": user.id, "role": user.role, "capabilities": caps})
    else:
        console().print(f"{user.id} ({user.role}): {', '.join(caps) or '—'}")


@app.command()
def grant(ctx: typer.Context, ref: RefArg, capability: CapArg) -> None:
    """Grant a user a capability."""
    from gaia.acl import capability_error
    from gaia.users import UserStore

    if err := capability_error(capability):  # a typo like 'reminder' fails loudly, not silently
        console().print(f"[red]{err}[/]")
        raise typer.Exit(1)
    store = UserStore()
    user_id = _resolve(store, ref)
    updated = store.grant(user_id, capability) if user_id else None
    if updated is None:
        console().print(f"no user matching {ref!r}")
        raise typer.Exit(1)
    console().print(
        f"granted {capability!r} to {updated.id} (grants: {', '.join(updated.grants) or '—'})"
    )


@app.command()
def revoke(ctx: typer.Context, ref: RefArg, capability: CapArg) -> None:
    """Revoke a capability from a user."""
    from gaia.acl import capability_error
    from gaia.users import UserStore

    if err := capability_error(capability):
        console().print(f"[red]{err}[/]")
        raise typer.Exit(1)
    store = UserStore()
    user_id = _resolve(store, ref)
    updated = store.revoke(user_id, capability) if user_id else None
    if updated is None:
        console().print(f"no user matching {ref!r}")
        raise typer.Exit(1)
    console().print(
        f"revoked {capability!r} from {updated.id} (denies: {', '.join(updated.denies) or '—'})"
    )
