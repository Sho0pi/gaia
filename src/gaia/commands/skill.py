"""``/skill`` — manage skills from chat (and the surface Gaia drives via run_command).

One command with sub-actions (``list`` / ``show`` / ``search`` / ``install`` / ``remove``)
so the model can install a skill by name or url mid-conversation. Requires the ``skills``
ACL capability (held by ``user``/``admin``), so the ``run_command`` tool may run it for a
caller who holds it. Reuses the same ``skills.py`` primitives as the ``gaia skill`` CLI.
"""

from __future__ import annotations

from gaia.commands.base import Command, CommandContext


class SkillCommand(Command):
    name = "skill"
    summary = "Manage skills: list, show, search, install, remove (id/glob/all)."
    usage = "<list|show|search|install|remove> [args]"
    capability = "skills"

    async def run(self, ctx: CommandContext) -> str:
        from gaia.skills import resolve_skills_dir

        sub, _, rest = ctx.args.strip().partition(" ")
        sub, rest = sub.lower(), rest.strip()
        skills_dir = resolve_skills_dir(ctx.gaia.config)

        if sub == "list":
            return _list(skills_dir)
        if sub == "show":
            return _show(skills_dir, rest)
        if sub == "search":
            return await _search(ctx, rest)
        if sub == "install":
            return _install(skills_dir, rest)
        if sub in ("remove", "uninstall", "rm"):
            return _remove(skills_dir, rest)
        return "Usage: /skill <list|show|search|install|remove> [args]"


def _list(skills_dir: object) -> str:
    from gaia.skills import list_skill_ids, load_skill

    ids = list_skill_ids(skills_dir)  # type: ignore[arg-type]
    if not ids:
        return "No skills installed. Search for one with /skill search <query>."
    lines = []
    for skill_id in ids:
        skill = load_skill(skills_dir, skill_id)  # type: ignore[arg-type]
        desc = skill.frontmatter.description if skill is not None else "(invalid)"
        lines.append(f"- {skill_id}: {desc}")
    return "Installed skills:\n" + "\n".join(lines)


def _show(skills_dir: object, skill_id: str) -> str:
    from gaia.skills import load_skill

    if not skill_id:
        return "Usage: /skill show <id>"
    skill = load_skill(skills_dir, skill_id)  # type: ignore[arg-type]
    if skill is None:
        return f"No skill named {skill_id!r} (try /skill list)."
    return f"{skill.frontmatter.name}: {skill.frontmatter.description}\n\n{skill.instructions}"


def _install(skills_dir: object, source: str) -> str:
    from gaia.skills import install_skill

    if not source:
        return "Usage: /skill install <path-or-git-url>"
    try:
        ids = install_skill(skills_dir, source)  # type: ignore[arg-type]
    except (FileNotFoundError, FileExistsError, ValueError, RuntimeError) as exc:
        return f"Install failed: {exc}"
    from gaia.state import commit_change

    commit_change(f"skill: installed {', '.join(ids)}", f"source: {source}")
    return f"Installed: {', '.join(ids)}. Ready to use right away."


def _remove(skills_dir: object, rest: str) -> str:
    from gaia.skills import remove_skills

    patterns = rest.split()
    if not patterns:
        return "Usage: /skill remove <id|glob|all> [more…]  (e.g. /skill remove huashu-* )"
    removed = remove_skills(skills_dir, patterns)  # type: ignore[arg-type]
    if not removed:
        return f"No skills matched {' '.join(patterns)!r} (try /skill list)."
    from gaia.state import commit_change

    commit_change(f"skill: removed {', '.join(removed)}")
    return f"Removed {len(removed)} skill(s): {', '.join(removed)}."


async def _search(ctx: CommandContext, query: str) -> str:
    from gaia.skills import skill_search

    if not query:
        return "Usage: /skill search <query>"
    cfg = ctx.gaia.config
    provider = None
    web_cfg = cfg.tools.get("web_search")
    engine = (web_cfg.model_extra or {}).get("engine") if web_cfg is not None else None
    if engine:
        from gaia.tools.web_search import get_search_provider

        try:
            provider = get_search_provider(str(engine))
        except ValueError:
            provider = None

    hits = await skill_search(query, index=list(cfg.skill_index), search_provider=provider)
    if not hits:
        return (
            f"No skills found for {query!r}. You can install a git url directly with "
            "/skill install <url>."
        )
    lines = [f"- {h['name']}: {h['description']}\n  source: {h['source']}" for h in hits]
    return (
        f"Skills matching {query!r}:\n"
        + "\n".join(lines)
        + "\n\nInstall one with /skill install <source>."
    )
