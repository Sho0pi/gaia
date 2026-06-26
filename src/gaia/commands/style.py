"""``/style`` — show or set Gaia's communication style (voice) from chat."""

from __future__ import annotations

from gaia.commands.base import Command, CommandContext
from gaia.communication import STYLES, current_style, set_style


class StyleCommand(Command):
    name = "style"
    summary = "Show or set Gaia's voice (/style <human|caveman|ai>)."
    usage = "[human|caveman|ai]"
    #: Changes global config (gaia.yaml), so it's gated like the other admin commands.
    capability = "manage_users"

    async def run(self, ctx: CommandContext) -> str:
        target = ctx.args.strip().lower()
        if not target:
            return f"Style: {current_style(ctx.gaia.config)}  (options: {', '.join(STYLES)})"
        try:
            set_style(ctx.gaia.settings.config_path, target)
        except ValueError as exc:
            return str(exc)
        return f"Style set to {target!r}. In effect from your next message."
