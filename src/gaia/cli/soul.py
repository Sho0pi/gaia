"""``gaia soul`` command group: manage reusable specialist subagents from the shell.

Direct CRUD over :class:`~gaia.agents.registry.SoulRegistry` (one ``<key>.md`` per
:class:`~gaia.agents.spec.AgentSpec` under ``~/.gaia/agent_registry/``). Everything is
**offline and key-free by default**; only ``create --ai`` opts into the soul-smith (an
LLM that authors the spec from a task) and therefore needs a model key.

Lazy-import rule (repo convention): only typer + stdlib (+ cli siblings) at module
level. ADK / the smith are imported inside ``create``'s ``--ai`` branch so ``gaia
soul --help`` and the manual commands never pay for them.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import click
import typer

from gaia.cli._console import console, emit_json
from gaia.cli._options import state

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.agents import AgentSpec, SoulRegistry
    from gaia.config import GaiaConfig, Settings
    from gaia.souls.smith import SoulDecision

app = typer.Typer(
    name="soul", help="Create, inspect, and manage reusable souls.", no_args_is_help=True
)

#: How wide a description may be in the `list` table before it is truncated.
_DESC_WIDTH = 60


def _registry(ctx: typer.Context) -> SoulRegistry:
    from gaia.agents import SoulRegistry
    from gaia.config import get_settings

    return SoulRegistry(get_settings(state(ctx).env_file).agent_registry_dir)


def _settings_and_config(ctx: typer.Context) -> tuple[Settings, GaiaConfig]:
    from gaia.config import ConfigSupplier, get_settings

    settings = get_settings(state(ctx).env_file)
    return settings, ConfigSupplier(settings.config_path).current


def _truncate(text: str, width: int = _DESC_WIDTH) -> str:
    return text if len(text) <= width else f"{text[: width - 1]}…"


def _spec_dict(spec: AgentSpec) -> dict[str, object]:
    """The list/`--json` projection of a soul (used by the `list` table and JSON output)."""
    return {
        "key": spec.key,
        "name": spec.name,
        "model": spec.model,
        "description": spec.description,
    }


@app.command("list")
def list_souls(ctx: typer.Context) -> None:
    """List every stored soul (key, name, model, description)."""
    registry = _registry(ctx)
    specs = [s for s in (registry.get(k) for k in registry.list_keys()) if s is not None]
    st = state(ctx)
    if st.json:
        emit_json({"souls": [_spec_dict(s) for s in specs]})
        return
    out = console()
    if not specs:
        out.print("no souls yet — create one with 'gaia soul create <name> …'")
        return
    from rich.table import Table

    table = Table(show_edge=False, pad_edge=False)
    for col in ("key", "name", "model", "description"):
        table.add_column(col)
    for spec in specs:
        table.add_row(spec.key, spec.name, spec.model, _truncate(spec.description))
    out.print(table)


@app.command()
def show(ctx: typer.Context, key: Annotated[str, typer.Argument(help="The soul key.")]) -> None:
    """Print every field of one soul (raw JSON with --json)."""
    spec = _registry(ctx).get(key)
    if spec is None:
        console().print(f"no soul named {key!r} (try 'gaia soul list')")
        raise typer.Exit(1)
    if state(ctx).json:
        print(spec.model_dump_json(indent=2))
        return
    out = console()
    out.print(f"[bold]{spec.name}[/]  ([cyan]{spec.key}[/])")
    out.print(f"model: {spec.model}")
    out.print(f"style: {spec.communication_style or '(default)'}")
    out.print(f"skills: {', '.join(spec.skills) or '(none)'}")
    out.print(f"tools: {', '.join(spec.tools) or '(all)'}")
    out.print(f"\ndescription:\n  {spec.description}")
    out.print(f"\ninstruction:\n{spec.instruction}")


@app.command()
def create(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Human name for the soul (slugified into its key).")],
    description: Annotated[
        str | None, typer.Option("--description", help="What the soul is for.")
    ] = None,
    instruction: Annotated[
        str | None, typer.Option("--instruction", help="The soul's system prompt.")
    ] = None,
    instruction_file: Annotated[
        Path | None,
        typer.Option("--instruction-file", help="Read the instruction from this file instead."),
    ] = None,
    model: Annotated[
        str | None, typer.Option("--model", help="Model id (default: config llm.model).")
    ] = None,
    skill: Annotated[
        list[str] | None, typer.Option("--skill", help="Skill id (repeatable).")
    ] = None,
    tool: Annotated[
        list[str] | None, typer.Option("--tool", help="Tool id to pin (repeatable).")
    ] = None,
    style: Annotated[
        str | None,
        typer.Option("--style", help="Voice (default: config default_communication_style)."),
    ] = None,
    ai: Annotated[
        str | None,
        typer.Option(
            "--ai", help="Let the soul-smith author the spec from this TASK (needs a model key)."
        ),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing soul with the same key.")
    ] = False,
    no_input: Annotated[
        bool, typer.Option("--no-input", help="Never prompt; fail if a field is missing.")
    ] = False,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="With --ai: skip confirmation prompts.")
    ] = False,
) -> None:
    """Create a soul — manually (no model key) or, with --ai, via the soul-smith."""
    registry = _registry(ctx)
    settings, cfg = _settings_and_config(ctx)
    out = console()

    if ai is not None:
        spec = _create_ai(registry, settings, cfg, name, ai, yes)
    else:
        spec = _create_manual(
            cfg,
            name,
            description,
            instruction,
            instruction_file,
            model,
            skill,
            tool,
            style,
            no_input,
        )

    if spec.key in registry.list_keys() and not force:
        out.print(f"soul {spec.key!r} already exists — pass --force to overwrite")
        raise typer.Exit(1)
    registry.save(spec)
    out.print(f"saved soul {spec.key!r} ({spec.name})")


def _create_manual(
    cfg: GaiaConfig,
    name: str,
    description: str | None,
    instruction: str | None,
    instruction_file: Path | None,
    model: str | None,
    skill: list[str] | None,
    tool: list[str] | None,
    style: str | None,
    no_input: bool,
) -> AgentSpec:
    """Build an AgentSpec from flags, prompting for missing required fields when allowed."""
    from pydantic import ValidationError

    from gaia.agents import AgentSpec

    if instruction is not None and instruction_file is not None:
        raise typer.BadParameter("pass --instruction or --instruction-file, not both")
    if instruction_file is not None:
        instruction = instruction_file.read_text()

    description = _require("description", description, no_input)
    instruction = _require("instruction", instruction, no_input)

    try:
        return AgentSpec(
            name=name,
            description=description,
            instruction=instruction,
            model=model or cfg.llm.model,
            skills=skill or [],
            tools=tool or [],
            communication_style=style or cfg.default_communication_style,
        )
    except ValidationError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _require(field: str, value: str | None, no_input: bool) -> str:
    """Return ``value``, prompting for it when missing — or exit 2 under ``--no-input``."""
    if value is not None:
        return value
    if no_input:
        console().print(f"missing required --{field} (and --no-input is set)")
        raise typer.Exit(2)
    return str(typer.prompt(field))


def _create_ai(
    registry: SoulRegistry, settings: Settings, cfg: GaiaConfig, name: str, task: str, yes: bool
) -> AgentSpec:
    """Run the soul-smith on ``task``; honour a reuse suggestion, else take the forged spec."""
    out = console()
    existing = _existing_souls(registry)
    try:
        decision = _forge(settings, cfg, task, existing)
        if decision.action == "reuse":
            reused = registry.get(decision.soul_key or "")
            label = f"{decision.soul_key}" + (f" — {reused.description}" if reused else "")
            out.print(f"the smith suggests reusing an existing soul: {label}")
            out.print(f"reason: {decision.reason}")
            if yes or typer.confirm("reuse it (instead of creating a new soul)?"):
                if reused is not None:
                    out.print(reused.model_dump_json(indent=2))
                raise typer.Exit(0)
            decision = _forge(settings, cfg, task, "(none yet)")  # force a forge
    except typer.Exit:
        raise
    except Exception as exc:  # smith / model failure
        out.print(f"soul-smith failed: {exc}")
        raise typer.Exit(1) from exc

    if decision.spec is None:
        out.print("soul-smith returned no spec")
        raise typer.Exit(1)
    spec = decision.spec.model_copy(update={"name": name})  # operator chooses the key
    out.print(spec.model_dump_json(indent=2))
    if not yes and not typer.confirm("save this soul?"):
        raise typer.Exit(0)
    return spec


def _existing_souls(registry: SoulRegistry) -> str:
    """Render known souls as ``key: description`` lines for the smith (or '(none yet)')."""
    lines = [f"{s.key}: {s.description}" for k in registry.list_keys() if (s := registry.get(k))]
    return "\n".join(lines) or "(none yet)"


def _forge(settings: Settings, cfg: GaiaConfig, task: str, existing: str) -> SoulDecision:
    """Run the soul-smith one-shot and return its decision (lazy ADK import lives here)."""
    import asyncio

    from gaia.config import configure_adk_env

    configure_adk_env(settings)
    return asyncio.run(_run_smith(cfg, task, existing))


async def _run_smith(cfg: GaiaConfig, task: str, existing: str) -> SoulDecision:
    """Drive the smith via a nested Runner; parse its structured final response."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from gaia import constants
    from gaia.souls.smith import SoulDecision, build_soul_smith

    smith = build_soul_smith(
        cfg.llm.model, provider=cfg.llm.provider, use_oauth=cfg.llm.openai.use_oauth
    )
    session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
    await session_service.create_session(
        app_name=constants.APP_NAME, user_id="cli", session_id="soul-create"
    )
    runner = Runner(app_name=constants.APP_NAME, agent=smith, session_service=session_service)
    request = f"TASK:\n{task}\n\nEXISTING SOULS:\n{existing}"
    content = types.Content(role="user", parts=[types.Part(text=request)])
    parts: list[str] = []
    try:
        async for event in runner.run_async(
            user_id="cli", session_id="soul-create", new_message=content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                parts.extend(p.text for p in event.content.parts if p.text)
    finally:
        await runner.close()  # type: ignore[no-untyped-call]
    return SoulDecision.model_validate_json("".join(parts))


@app.command()
def edit(ctx: typer.Context, key: Annotated[str, typer.Argument(help="The soul key.")]) -> None:
    """Open a soul's Markdown in $EDITOR, re-validate it on save, and store it."""
    from gaia.agents import AgentSpec

    registry = _registry(ctx)
    spec = registry.get(key)
    out = console()
    if spec is None:
        out.print(f"no soul named {key!r} (try 'gaia soul list')")
        raise typer.Exit(1)

    edited = click.edit(spec.to_markdown(), extension=".md")
    if edited is None:
        out.print("no changes")
        return
    try:
        new_spec = AgentSpec.from_markdown(edited)
    except ValueError as exc:
        out.print(f"invalid soul — not saved: {exc}")
        raise typer.Exit(1) from exc

    registry.save(new_spec)
    if new_spec.key != key:
        registry.delete(key)  # name changed → don't orphan the old file
        out.print(f"key changed {key!r} → {new_spec.key!r}")
    out.print(f"saved soul {new_spec.key!r}")


@app.command()
def delete(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument(help="The soul key.")],
    force: Annotated[bool, typer.Option("--force", help="Delete without confirmation.")] = False,
) -> None:
    """Delete a soul by key (asks for confirmation unless --force)."""
    registry = _registry(ctx)
    out = console()
    if registry.get(key) is None:
        out.print(f"no soul named {key!r} (try 'gaia soul list')")
        raise typer.Exit(1)
    if not force and not typer.confirm(f"delete soul {key!r}?"):
        raise typer.Exit(0)
    registry.delete(key)
    out.print(f"deleted soul {key!r}")
