"""``gaia skill`` command group: list, show, author, install, and remove skills.

A *skill* is a ``<skills_dir>/<id>/SKILL.md`` folder (see :mod:`gaia.skills`). Installed
skills are usable immediately — the root agent carries the on-demand ``SkillToolset``, so
nothing needs rebuilding. Everything here is offline and key-free except ``new --from``,
which has a model draft the skill body and so needs a model key.

Lazy-import rule (repo convention): only typer + stdlib (+ cli siblings) at module level;
ADK / the model are imported inside the commands that need them.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from gaia.cli._console import console, emit_json
from gaia.cli._options import state

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.config import GaiaConfig

app = typer.Typer(
    name="skill", help="List, author, install, and manage skills.", no_args_is_help=True
)

_DESC_WIDTH = 60


def _skills_dir(ctx: typer.Context) -> Path:
    from gaia.config import ConfigSupplier, get_settings
    from gaia.skills import resolve_skills_dir

    settings = get_settings(state(ctx).env_file)
    return resolve_skills_dir(ConfigSupplier(settings.config_path).current)


def _config(ctx: typer.Context) -> GaiaConfig:
    from gaia.config import ConfigSupplier, get_settings

    return ConfigSupplier(get_settings(state(ctx).env_file).config_path).current


def _truncate(text: str, width: int = _DESC_WIDTH) -> str:
    return text if len(text) <= width else f"{text[: width - 1]}…"


@app.command("list")
def list_skills(ctx: typer.Context) -> None:
    """List every installed skill (id + description)."""
    from gaia.skills import list_skill_ids, load_skill

    skills_dir = _skills_dir(ctx)
    rows = []
    for skill_id in list_skill_ids(skills_dir):
        skill = load_skill(skills_dir, skill_id)
        desc = skill.frontmatter.description if skill is not None else "(invalid)"
        rows.append((skill_id, desc))

    if state(ctx).json:
        emit_json({"skills": [{"id": i, "description": d} for i, d in rows]})
        return
    out = console()
    if not rows:
        out.print(f"no skills in {skills_dir} — author one with 'gaia skill new <name>'")
        return
    from rich.table import Table

    table = Table(show_edge=False, pad_edge=False)
    table.add_column("id")
    table.add_column("description")
    for skill_id, desc in rows:
        table.add_row(skill_id, _truncate(desc))
    out.print(table)


@app.command()
def show(
    ctx: typer.Context, skill_id: Annotated[str, typer.Argument(help="The skill id.")]
) -> None:
    """Print a skill's description and full instructions (SKILL.md body)."""
    from gaia.skills import load_skill

    skill = load_skill(_skills_dir(ctx), skill_id)
    if skill is None:
        console().print(f"no skill named {skill_id!r} (try 'gaia skill list')")
        raise typer.Exit(1)
    if state(ctx).json:
        emit_json(
            {
                "id": skill.frontmatter.name,
                "description": skill.frontmatter.description,
                "instructions": skill.instructions,
            }
        )
        return
    out = console()
    out.print(f"[bold]{skill.frontmatter.name}[/]")
    out.print(f"description: {skill.frontmatter.description}\n")
    out.print(skill.instructions)


@app.command()
def new(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Skill name (slugified into its id).")],
    description: Annotated[
        str | None, typer.Option("--description", help="One-line description.")
    ] = None,
    instruction_file: Annotated[
        Path | None,
        typer.Option("--instruction-file", help="Read the SKILL.md body from this file."),
    ] = None,
    from_: Annotated[
        str | None,
        typer.Option("--from", help="Have a model draft the skill from this one-line brief."),
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing skill.")] = False,
) -> None:
    """Author a new skill — manually, from a file, or drafted by a model (--from)."""
    from gaia.skills import skill_id_for, write_skill

    skills_dir = _skills_dir(ctx)
    out = console()

    if from_ is not None:
        description, instructions = _draft_skill(_config(ctx), name, from_)
    elif instruction_file is not None:
        instructions = instruction_file.read_text()
        description = description or name
    else:
        out.print("provide --from <brief>, or --instruction-file <path> (+ --description)")
        raise typer.Exit(1)

    if force:
        from gaia.skills import list_skill_ids

        if skill_id_for(name) in list_skill_ids(skills_dir):
            import shutil

            shutil.rmtree(skills_dir / skill_id_for(name), ignore_errors=True)
    try:
        folder = write_skill(skills_dir, name, description or name, instructions)
    except (FileExistsError, ValueError) as exc:
        out.print(f"could not create skill: {exc}")
        raise typer.Exit(1) from exc
    out.print(f"created skill {folder.name!r} at {folder}")


@app.command()
def install(
    ctx: typer.Context,
    source: Annotated[str, typer.Argument(help="A local path or a git url (url#subdir).")],
    name: Annotated[
        str | None, typer.Option("--name", help="Rename the id (single-skill installs only).")
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite existing skills.")] = False,
) -> None:
    """Install skill(s) from a local folder or a git repo into the skills dir."""
    from gaia.skills import install_skill

    try:
        ids = install_skill(_skills_dir(ctx), source, name=name, force=force)
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        console().print(f"install failed: {exc}")
        raise typer.Exit(1) from exc
    console().print(f"installed: {', '.join(ids)}")


@app.command()
def remove(
    ctx: typer.Context,
    skill_id: Annotated[str, typer.Argument(help="The skill id to delete.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation.")] = False,
) -> None:
    """Delete a skill folder from the skills dir."""
    import shutil

    folder = _skills_dir(ctx) / skill_id
    out = console()
    if not folder.is_dir():
        out.print(f"no skill named {skill_id!r} (try 'gaia skill list')")
        raise typer.Exit(1)
    if not yes:
        typer.confirm(f"delete skill {skill_id!r} at {folder}?", abort=True)
    shutil.rmtree(folder, ignore_errors=True)
    out.print(f"removed skill {skill_id!r}")


def _draft_skill(cfg: GaiaConfig, name: str, brief: str) -> tuple[str, str]:
    """Have a model draft (description, instructions) for a skill from a one-line brief."""
    import asyncio

    return asyncio.run(_draft_skill_async(cfg, name, brief))


async def _draft_skill_async(cfg: GaiaConfig, name: str, brief: str) -> tuple[str, str]:
    from google.adk.agents import LlmAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    from pydantic import BaseModel

    from gaia import constants
    from gaia.models import resolve_model

    class SkillDraft(BaseModel):
        description: str
        instructions: str

    drafter = LlmAgent(
        name="skill_drafter",
        model=resolve_model(
            cfg.llm.model, provider=cfg.llm.provider, use_oauth=cfg.llm.openai.use_oauth
        ),
        description="Authors a reusable skill (prompt knowledge) from a brief.",
        instruction=(
            "You write a SKILL for an AI agent — concise, reusable prompt knowledge (NOT code). "
            "Given a skill name and a one-line brief, return a one-sentence description and a "
            "markdown instructions body the agent should follow when the skill applies. Be "
            "specific and actionable; no preamble."
        ),
        output_schema=SkillDraft,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )
    session_service = InMemorySessionService()  # type: ignore[no-untyped-call]
    await session_service.create_session(
        app_name=constants.APP_NAME, user_id="cli", session_id="skill-draft"
    )
    runner = Runner(app_name=constants.APP_NAME, agent=drafter, session_service=session_service)
    content = types.Content(
        role="user", parts=[types.Part(text=f"Skill name: {name}\nBrief: {brief}")]
    )
    parts: list[str] = []
    try:
        async for event in runner.run_async(
            user_id="cli", session_id="skill-draft", new_message=content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                parts.extend(p.text for p in event.content.parts if p.text)
    finally:
        await runner.close()  # type: ignore[no-untyped-call]
    draft = SkillDraft.model_validate_json("".join(parts))
    return draft.description, draft.instructions
