"""Load Anthropic/ADK-style skills and attach them to agents.

A *skill* is a folder ``<skills_dir>/<id>/SKILL.md`` (YAML frontmatter + markdown
body). We reuse ADK's native loader (:func:`google.adk.skills.load_skill_from_dir`)
rather than parsing ``SKILL.md`` ourselves — it returns a ``Skill`` whose
``.instructions`` is the body and ``.frontmatter`` carries name/description. ADK
enforces that the folder name matches the frontmatter ``name``.

This module is the single resolver both call sites use: the factory (dynamic
``AgentSpec.skills``) and the root Gaia agent (config-bound skills). Attaching a skill
*always-on* means appending its instructions to the agent's system prompt, so the
behaviour can't be skipped — distinct from ADK's ``SkillToolset`` progressive
disclosure where the model loads skills on demand (a planned follow-up).

The ADK import is deferred so importing gaia stays cheap and unit tests need no
model backend (the loader itself is pure file parsing).
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from gaia import constants

if TYPE_CHECKING:  # pragma: no cover - typing only
    from google.adk.skills import Skill
    from google.adk.tools.base_toolset import BaseToolset

    from gaia.config import GaiaConfig

logger = logging.getLogger(__name__)


def resolve_skills_dir(config: GaiaConfig, *, default: Path = constants.SKILLS_DIR) -> Path:
    """Where skills are loaded from: ``config.skills_dir`` if set, else the default."""
    configured = config.skills_dir
    if configured is not None and str(configured) not in ("", "."):
        return Path(configured)
    return default


def load_skill(skills_dir: Path, skill_id: str) -> Skill | None:
    """Load one skill by id, or ``None`` if its folder is missing/invalid.

    Missing is expected and non-fatal: an id may name a skill that hasn't been
    downloaded yet, so we warn and skip rather than raise.
    """
    from google.adk.skills import load_skill_from_dir

    try:
        return load_skill_from_dir(Path(skills_dir) / skill_id)
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("skill %r not loaded from %s: %s", skill_id, skills_dir, exc)
        return None


def build_skill_toolset(skills_dir: Path) -> BaseToolset | None:
    """Build an ADK ``SkillToolset`` exposing every skill under ``skills_dir`` on demand.

    Returns a toolset that gives the model the progressive-disclosure tools
    (``list_skills`` / ``load_skill`` / ``load_skill_resource``), so an agent can discover
    and pull in a skill's instructions only when a task needs them — distinct from the
    always-on injection of :func:`attach_skills`. ``None`` when the folder is missing or
    holds no valid skill, so callers never attach an empty toolset. A malformed skill is
    skipped (warned), never fatal. ADK is imported lazily (heavy-deps convention).
    """
    skills_dir = Path(skills_dir)
    if not skills_dir.is_dir():
        return None
    from google.adk.skills import list_skills_in_dir
    from google.adk.tools.skill_toolset import SkillToolset

    skills: list[Skill] = []
    for skill_id in list_skills_in_dir(skills_dir):
        skill = load_skill(skills_dir, skill_id)  # warns + returns None on a bad folder
        if skill is not None:
            skills.append(skill)
    if not skills:
        return None
    return SkillToolset(skills=skills)


def attach_skills(base_instruction: str, skill_ids: list[str], skills_dir: Path) -> str:
    """Return ``base_instruction`` with each resolved skill's instructions appended.

    Skills are appended in the given order under a labelled separator. Unknown ids
    are skipped (see :func:`load_skill`). With no skills the base is returned as-is.
    """
    sections = [base_instruction]
    for skill_id in skill_ids:
        skill = load_skill(skills_dir, skill_id)
        if skill is not None:
            sections.append(f"# Skill: {skill.frontmatter.name}\n\n{skill.instructions}")
    return "\n\n".join(sections)


def skill_id_for(name: str) -> str:
    """Normalize a proposed skill name into a kebab-case folder id."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "skill"


def write_skill(skills_dir: Path, name: str, description: str, instructions: str) -> Path:
    """Create ``skills_dir/<id>/SKILL.md`` and validate it loads; return the folder.

    ADK requires the folder name to equal the frontmatter ``name``, so both come from
    :func:`skill_id_for`. Refuses to overwrite an existing skill. The written folder is
    round-tripped through ADK's loader — on failure it is removed and the error raised,
    so a half-written skill can never break startup loading.
    """
    skill_id = skill_id_for(name)
    folder = Path(skills_dir) / skill_id
    if folder.exists():
        raise FileExistsError(f"skill {skill_id!r} already exists at {folder}")

    front = yaml.safe_dump(
        {"name": skill_id, "description": description.strip()}, sort_keys=False, allow_unicode=True
    ).strip()
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\n{front}\n---\n\n{instructions.strip()}\n")

    if load_skill(skills_dir, skill_id) is None:  # warns with the underlying reason
        shutil.rmtree(folder, ignore_errors=True)
        raise ValueError(f"written skill {skill_id!r} failed ADK validation — removed")
    return folder


def list_skill_ids(skills_dir: Path) -> list[str]:
    """Every skill id under ``skills_dir`` (ADK loader's view); empty if missing."""
    skills_dir = Path(skills_dir)
    if not skills_dir.is_dir():
        return []
    from google.adk.skills import list_skills_in_dir

    return sorted(list_skills_in_dir(skills_dir))


async def skill_search(
    query: str,
    *,
    index: list[str],
    search_provider: Callable[[str, int, str | None], list[dict[str, str]]] | None = None,
    limit: int = 8,
) -> list[dict[str, str]]:
    """Find installable skills matching ``query`` — from the index, then a web fallback.

    Each index url points at a json manifest (a list of ``{name, description, source}``,
    where ``source`` is a git url / path :func:`install_skill` accepts). Entries whose name
    or description contains ``query`` (case-insensitive) are returned. If none match and a
    ``search_provider`` (a ``gaia.tools.web_search`` provider) is given, fall back to a web
    search for SKILL.md repos and surface those urls as sources. Returns ``[]`` on no hits;
    never raises (network errors are logged + skipped).
    """
    import httpx

    q = query.strip().lower()
    hits: list[dict[str, str]] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for url in index:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                entries = resp.json()
            except Exception as exc:
                logger.warning("skill index %s unreadable: %s", url, exc)
                continue
            for entry in entries if isinstance(entries, list) else []:
                name = str(entry.get("name", ""))
                desc = str(entry.get("description", ""))
                src = str(entry.get("source", ""))
                if not src or src in seen:
                    continue
                if q in name.lower() or q in desc.lower():
                    seen.add(src)
                    hits.append({"name": name, "description": desc, "source": src})

    if not hits and search_provider is not None:
        import asyncio

        try:
            # SearchProvider is a sync callable (query, max_results, timelimit) -> dicts.
            results = await asyncio.to_thread(
                search_provider, f"{query} SKILL.md github", limit, None
            )
            for r in results:
                url = str(r.get("url", ""))
                if url and url not in seen:
                    seen.add(url)
                    hits.append(
                        {"name": r.get("title", url), "description": "(web result)", "source": url}
                    )
        except Exception as exc:
            logger.warning("skill web-search fallback failed: %s", exc)
    return hits[:limit]


def _looks_like_git(source: str) -> bool:
    """Heuristic: a git remote rather than a local path (scp-style or a URL scheme)."""
    return source.startswith(("git@", "ssh://")) or (
        "://" in source and not Path(source.split("#", 1)[0]).exists()
    )


def _skill_dirs_under(root: Path) -> list[Path]:
    """The skill folders under ``root``: ``root`` itself if it holds a SKILL.md, else its
    immediate children that do."""
    if (root / "SKILL.md").is_file():
        return [root]
    return sorted(d for d in root.iterdir() if d.is_dir() and (d / "SKILL.md").is_file())


def _install_one(src: Path, skills_dir: Path, *, dest_id: str, force: bool) -> str:
    """Copy one skill folder into ``skills_dir`` under ``dest_id``, validate, return the id."""
    dest = Path(skills_dir) / dest_id
    if dest.exists():
        if not force:
            raise FileExistsError(f"skill {dest_id!r} already exists — pass force to overwrite")
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    if src.name != dest_id:
        _rename_skill_frontmatter(dest / "SKILL.md", dest_id)  # ADK requires name == folder
    if load_skill(skills_dir, dest_id) is None:  # round-trip through ADK's loader
        shutil.rmtree(dest, ignore_errors=True)
        raise ValueError(f"skill {dest_id!r} failed ADK validation — removed")
    return dest_id


def _rename_skill_frontmatter(skill_md: Path, new_name: str) -> None:
    """Rewrite a SKILL.md's frontmatter ``name`` so it matches a renamed folder."""
    text = skill_md.read_text()
    if not text.startswith("---"):
        raise ValueError("SKILL.md has no frontmatter")
    _, front, body = text.split("---", 2)
    data = yaml.safe_load(front) or {}
    data["name"] = new_name
    dumped = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip()
    skill_md.write_text(f"---\n{dumped}\n---{body}")


def install_skill(
    skills_dir: Path, source: str, *, name: str | None = None, force: bool = False
) -> list[str]:
    """Install skill(s) from a local path or a git url into ``skills_dir``; return the ids.

    ``source`` is either a local path (a SKILL.md folder, or a folder of them) or a git url
    (optionally ``url#subdir`` to pick a folder inside the repo). ``name`` renames the id
    when installing exactly one skill. Each copied skill is validated through ADK's loader;
    an invalid one is removed and raises. Existing ids are refused unless ``force``.
    """
    skills_dir = Path(skills_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)

    if _looks_like_git(source):
        url, _, subdir = source.partition("#")
        tmp = Path(tempfile.mkdtemp(prefix="gaia-skill-"))
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", url, str(tmp)],
                check=True,
                capture_output=True,
                text=True,
            )
            root = tmp / subdir if subdir else tmp
            if not root.is_dir():
                raise FileNotFoundError(f"{subdir!r} not found in the cloned repo")
            return _install_from_root(root, skills_dir, name=name, force=force)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"git clone failed: {exc.stderr.strip() or exc}") from exc
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    root = Path(source).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"no such directory: {root}")
    return _install_from_root(root, skills_dir, name=name, force=force)


def _install_from_root(root: Path, skills_dir: Path, *, name: str | None, force: bool) -> list[str]:
    folders = _skill_dirs_under(root)
    if not folders:
        raise FileNotFoundError(f"no SKILL.md found under {root}")
    if name is not None and len(folders) > 1:
        raise ValueError("name can only be set when installing a single skill")
    installed = []
    for folder in folders:
        dest_id = skill_id_for(name) if name is not None else folder.name
        installed.append(_install_one(folder, skills_dir, dest_id=dest_id, force=force))
    return installed
