"""``/help`` — list the commands you can run (grouped), or show one command's full help."""

from __future__ import annotations

from gaia.commands import catalog
from gaia.commands.base import PREFIX, Command, CommandContext, authorize

#: A few common sub-actions that aren't standalone commands — point /help at their parent.
_SUBCOMMAND_PARENT = {
    "approve": "task",
    "reject": "task",
    "answer": "task",
    "list": "skill",
    "show": "skill",
    "search": "skill",
    "install": "skill",
    "remove": "skill",
}


class HelpCommand(Command):
    name = "help"

    async def run(self, ctx: CommandContext) -> str:
        arg = ctx.args.strip().lstrip(PREFIX).lower()
        return _command_help(arg, ctx) if arg else _command_list(ctx)


def _command_help(name: str, ctx: CommandContext) -> str:
    """The rich help for one command, or a friendly hint when ``name`` isn't a command."""
    cmd = ctx.registry.get(name)
    if cmd is not None:
        return cmd.full_help()
    parent = _SUBCOMMAND_PARENT.get(name)
    if parent is not None:
        return f"{PREFIX}{name} is an action of {PREFIX}{parent} — try {PREFIX}help {parent}."
    return f"No command {PREFIX}{name}. Send {PREFIX}help to see them all."


def _command_list(ctx: CommandContext) -> str:
    """The grouped list of commands the caller can run — bold section headers (WhatsApp-native)."""
    runnable = [c for c in ctx.registry.all() if authorize(c, ctx) is None]
    by_category: dict[str, list[Command]] = {}
    for c in runnable:
        meta = catalog.info(c.name)
        by_category.setdefault(meta.category if meta else "", []).append(c)

    extra = sorted(k for k in by_category if k not in catalog.CATEGORY_ORDER)
    lines = ["*Gaia — commands*"]
    for category in (*catalog.CATEGORY_ORDER, *extra):
        cmds = by_category.get(category)
        if not cmds:
            continue
        lines += ["", f"*{category or 'Other'}*"]
        lines += [f"{PREFIX}{c.name} — {c.summary}" for c in sorted(cmds, key=lambda c: c.name)]
    lines += ["", f"_{PREFIX}help <command> for details_"]
    return "\n".join(lines)
