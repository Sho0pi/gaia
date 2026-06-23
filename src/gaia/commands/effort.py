"""``/effort`` — show or set the model's reasoning effort from chat."""

from __future__ import annotations

from gaia.commands.base import Command, CommandContext
from gaia.models import _gemini_thinks

#: Levels we accept. ``minimal``/``max`` are provider-specific but pass straight through to
#: litellm / the OAuth backend, which validate them. ``off``/``none``/`""` clear the setting.
_LEVELS = ("minimal", "low", "medium", "high", "max")
_CLEAR = ("off", "none", "")


def _supports_effort(provider: str, model: str) -> bool:
    """Whether the current provider/model has a reasoning dial we can drive."""
    prov = provider.lower()
    if prov == "gemini":
        return _gemini_thinks(model)
    return True  # openai / anthropic / other litellm reasoning models accept reasoning_effort


class EffortCommand(Command):
    name = "effort"
    summary = "Show or set the model's reasoning effort (/effort <minimal|low|medium|high>)."
    usage = "[minimal|low|medium|high|off]"
    #: Changes global config (gaia.yaml), so it's gated like the other admin commands.
    capability = "manage_users"

    async def run(self, ctx: CommandContext) -> str:
        llm = ctx.gaia.config.llm
        model = llm.model or ctx.gaia.settings.model
        unsupported = (
            ""
            if _supports_effort(llm.provider, model)
            else f"\n(Note: {model} has no reasoning dial — this has no effect on it.)"
        )

        target = ctx.args.strip().lower()
        if not target:
            return f"Effort: {llm.effort or '(default)'}  (model: {model}){unsupported}"

        if target in _CLEAR:
            self._write(ctx, "")
            return "Effort cleared (provider default). In effect from your next message."
        if target not in _LEVELS:
            return f"Unknown effort '{target}'. Use one of: {', '.join(_LEVELS)}, or off."

        self._write(ctx, target)
        return f"Effort set to {target!r}. In effect from your next message.{unsupported}"

    @staticmethod
    def _write(ctx: CommandContext, value: str) -> None:
        from gaia.cli._yamledit import set_config_value

        set_config_value(ctx.gaia.settings.config_path, "llm.effort", value)
