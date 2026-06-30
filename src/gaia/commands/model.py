"""``/model`` — show the active model, or switch it from chat."""

from __future__ import annotations

from gaia.commands.base import Command, CommandContext


class ModelCommand(Command):
    name = "model"
    #: Changes global config (gaia.yaml), so it's gated like the other admin commands.
    capability = "manage_users"

    async def run(self, ctx: CommandContext) -> str:
        llm = ctx.gaia.config.llm
        target = ctx.args.strip()
        if not target:
            return f"Model: {llm.model or '(default)'}  (provider: {llm.provider})"

        from gaia.cli._yamledit import set_config_value

        set_config_value(ctx.gaia.settings.config_path, "llm.model", target)
        return (
            f"Model set to {target!r} (provider: {llm.provider}). In effect from your next message."
        )
