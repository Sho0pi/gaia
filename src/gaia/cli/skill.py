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
from typing import Annotated

import typer

from gaia.cli._console import console, emit_json
from gaia.cli._options import state

# Argument/option types named once so the command signatures below stay readable.
SkillIdArg = Annotated[str, typer.Argument(help="The skill id.")]
NameArg = Annotated[str, typer.Argument(help="Skill name (slugified into its id).")]
SourceArg = Annotated[str, typer.Argument(help="A local path or a git url (url#subdir).")]
PatternsArg = Annotated[
    list[str], typer.Argument(help="Skill ids or globs to delete; 'all' for everything.")
]
DescriptionOpt = Annotated[str | None, typer.Option("--description", help="One-line description.")]
InstructionFileOpt = Annotated[
    Path | None, typer.Option("--instruction-file", help="Read the SKILL.md body from this file.")
]
FromOpt = Annotated[
    str | None,
    typer.Option("--from", help="Have a model draft the skill from this one-line brief."),
]
NameOpt = Annotated[
    str | None, typer.Option("--name", help="Rename the id (single-skill installs only).")
]
ForceOpt = Annotated[bool, typer.Option("--force", help="Overwrite existing skill(s).")]
YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation.")]

app = typer.Typer(
    name="skill", help="List, author, install, and manage skills.", no_args_is_help=True
)

_DESC_WIDTH = 60


def _skills_dir(ctx: typer.Context) -> Path:
    from gaia.config import ConfigSupplier, get_settings
    from gaia.skills import resolve_skills_dir

    settings = get_settings(state(ctx).env_file)
    return resolve_skills_dir(ConfigSupplier(settings.config_path).current)


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
def show(ctx: typer.Context, skill_id: SkillIdArg) -> None:
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
    name: NameArg,
    description: DescriptionOpt = None,
    instruction_file: InstructionFileOpt = None,
    from_: FromOpt = None,
    force: ForceOpt = False,
) -> None:
    """Author a new skill — manually, from a file, or drafted by a model (--from)."""
    from gaia.skills import skill_id_for, write_skill

    skills_dir = _skills_dir(ctx)
    out = console()

    if from_ is not None:
        description, instructions = _draft(ctx, name, from_)
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
    from gaia.state import commit_change

    commit_change(f"skill: created '{folder.name}'", description or "")
    out.print(f"created skill {folder.name!r} at {folder}")


@app.command()
def install(
    ctx: typer.Context,
    source: SourceArg,
    name: NameOpt = None,
    force: ForceOpt = False,
) -> None:
    """Install skill(s) from a local folder or a git repo into the skills dir."""
    from gaia.skills import install_skill

    try:
        ids = install_skill(_skills_dir(ctx), source, name=name, force=force)
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        console().print(f"install failed: {exc}")
        raise typer.Exit(1) from exc
    from gaia.state import commit_change

    commit_change(f"skill: installed {', '.join(ids)}", f"source: {source}")
    console().print(f"installed: {', '.join(ids)}")


@app.command("rm")
def remove(
    ctx: typer.Context,
    patterns: PatternsArg,
    yes: YesOpt = False,
) -> None:
    """Delete skills by id, glob (huashu-*), or 'all'."""
    from gaia.skills import list_skill_ids, remove_skills

    skills_dir = _skills_dir(ctx)
    out = console()
    # Resolve the match set first so we can confirm before deleting anything.
    import fnmatch

    ids = list_skill_ids(skills_dir)
    matched = (
        list(ids)
        if any(p.strip().lower() in ("all", "*") for p in patterns)
        else sorted({i for i in ids for p in patterns if i == p or fnmatch.fnmatch(i, p)})
    )
    if not matched:
        out.print(f"no skills matched {' '.join(patterns)!r} (try 'gaia skill list')")
        raise typer.Exit(1)
    if not yes:
        typer.confirm(f"delete {len(matched)} skill(s): {', '.join(matched)}?", abort=True)
    removed = remove_skills(skills_dir, patterns)
    from gaia.state import commit_change

    commit_change(f"skill: removed {', '.join(removed)}")
    out.print(f"removed {len(removed)} skill(s): {', '.join(removed)}")


# `remove` stays as a hidden alias for `rm` (back-compat).
app.command("remove", hidden=True)(remove)


def _draft(ctx: typer.Context, name: str, brief: str) -> tuple[str, str]:
    """Build a Gaia so the skill author can research (tools + memory), then draft + close it."""
    import asyncio

    from gaia.agents.skill_author import draft_skill
    from gaia.config import get_settings
    from gaia.core import Gaia

    gaia = Gaia(get_settings(state(ctx).env_file))
    try:
        return draft_skill(gaia, name, brief)
    finally:
        asyncio.run(gaia.close())
