"""``/memory`` - list memories, or (admin) toggle memory + switch the embedder provider."""

from __future__ import annotations

import shutil

from gaia import constants
from gaia.commands.base import Command, CommandContext

#: Embedder providers offered in chat (Gemini is free; OpenAI needs an OPENAI_API_KEY). Local/ONNX
#: is a follow-up (mem0's built-in local embedders pull torch / need ollama).
_PROVIDERS = ("gemini", "openai")


class MemoryCommand(Command):
    name = "memory"

    async def run(self, ctx: CommandContext) -> str:
        arg = ctx.args.strip().lower()
        if not arg:
            return await self._status_and_list(ctx)
        if arg in ("on", "off"):
            return self._toggle(ctx, on=arg == "on")
        if arg in _PROVIDERS:
            return self._set_provider(ctx, arg)
        return "Usage: /memory [on|off|gemini|openai]"

    async def _status_and_list(self, ctx: CommandContext) -> str:
        """List the user's memories with a status header (open to everyone)."""
        mem = ctx.gaia.config.memory
        if not mem.enabled:
            return "Long-term memory is *off*. An admin can turn it on with /memory on."
        header = f"Long-term memory: *on* (embedder: {mem.embedder.provider})"
        service = ctx.gaia.memory_service
        if service is None:
            return header
        items = await service.list_memories(user_id=ctx.user_id)
        if not items:
            return f"{header}\nI don't remember anything about you yet."
        return f"{header}\nI remember:\n" + "\n".join(f"- {item}" for item in items)

    def _toggle(self, ctx: CommandContext, *, on: bool) -> str:
        if (refusal := _require_admin(ctx)) is not None:
            return refusal
        _write(ctx, "memory.enabled", on)  # checked per-access (core/agent.py) → takes effect live
        return "Long-term memory turned *on*." if on else "Long-term memory turned *off*."

    def _set_provider(self, ctx: CommandContext, provider: str) -> str:
        if (refusal := _require_admin(ctx)) is not None:
            return refusal
        _write(ctx, "memory.embedder.provider", provider)
        _write(ctx, "memory.enabled", True)
        _clear_store()  # old vectors live in the previous embedder's space - incompatible
        # The mem0 client is a build-once singleton, so the new embedder applies on restart.
        note = (
            f"Switched the memory embedder to *{provider}* and reset long-term memory (old vectors "
            "don't match a new embedder). Restart gaia to apply: `gaia restart`."
        )
        return note + _missing_key_warning(ctx, provider)


def _require_admin(ctx: CommandContext) -> str | None:
    """``None`` if the caller may change global memory settings, else a refusal string."""
    from gaia.acl import can

    user = ctx.gaia.users.get(ctx.user_id)
    allowed = ctx.role == "admin" if user is None else can(user, "manage_users", ctx.gaia.config)
    return None if allowed else "Only an admin can change memory settings."


def _write(ctx: CommandContext, key: str, value: object) -> None:
    from gaia.cli._yamledit import set_config_value

    set_config_value(ctx.gaia.settings.config_path, key, value)


def _clear_store() -> None:
    shutil.rmtree(constants.HOME_DIR / "memory" / "chroma", ignore_errors=True)


def _missing_key_warning(ctx: CommandContext, provider: str) -> str:
    settings = ctx.gaia.settings
    if provider == "gemini" and not settings.google_api_key:
        return "\n⚠️ No GEMINI_API_KEY set - get a free one at aistudio.google.com."
    if provider == "openai" and not settings.openai_api_key:
        return "\n⚠️ No OPENAI_API_KEY set - memory won't embed until you add one."
    return ""
