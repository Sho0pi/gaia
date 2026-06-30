"""Project resolution: route by description so one app doesn't fork, and switching works."""

from __future__ import annotations

from pathlib import Path

from gaia.souls.projects import ProjectStore, read_project_description, write_project_md
from gaia.souls.run import _existing_projects, resolve_project

# (slug, description) — the shape resolve_project/_existing_projects now use.
_HSK = ("chinese-flashcard-style", "HSK 1 flashcard webapp orange theme login-with-name")
_LANDING = ("landing-page", "marketing landing page for a coffee shop")


def _store(tmp_path: Path) -> ProjectStore:
    return ProjectStore(tmp_path / "projects.json")


def test_named_exact_match_reuses(tmp_path: Path) -> None:
    got = resolve_project("landing-page", "tweak it", "u", "fe", [_HSK, _LANDING], _store(tmp_path))
    assert got == "landing-page"


def test_invented_name_reuses_via_description(tmp_path: Path) -> None:
    # The model invents a NEW slug for the same app — description keywords (hsk/flashcard/login)
    # land it on the existing project instead of forking. (The exact Pi failure.)
    got = resolve_project(
        "hsk1-flashcards", "fix the hsk flashcards login bug", "u", "fe", [_HSK], _store(tmp_path)
    )
    assert got == "chinese-flashcard-style"


def test_omitted_switches_to_the_project_the_task_describes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set("u", "fe", "chinese-flashcard-style")  # currently on the HSK app
    got = resolve_project(
        "", "now work on the landing page hero", "u", "fe", [_HSK, _LANDING], store
    )
    assert got == "landing-page"  # switched by content, not stuck on 'current'


def test_genuinely_new_starts_a_new_dir(tmp_path: Path) -> None:
    got = resolve_project("todo", "build a todo app", "u", "fe", [_HSK], _store(tmp_path))
    assert got == "todo"  # no keyword overlap → new


def test_omitted_no_match_continues_last(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set("u", "fe", "chinese-flashcard-style")
    got = resolve_project("", "make the header bigger", "u", "fe", [_HSK, _LANDING], store)
    assert got == "chinese-flashcard-style"  # nothing matches → weakest fallback = current


def test_omitted_no_match_no_history_is_fresh(tmp_path: Path) -> None:
    store = _store(tmp_path)
    got = resolve_project("", "something totally unrelated zzz", "u", "fe", [_HSK], store)
    assert got != "chinese-flashcard-style" and store.get("u", "fe") == got


def test_resolution_is_recorded(tmp_path: Path) -> None:
    store = _store(tmp_path)
    resolve_project("todo", "build a todo app", "u", "fe", [_HSK], store)
    assert store.get("u", "fe") == "todo"


def test_store_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "projects.json"
    ProjectStore(path).set("u", "fe", "hsk-app")
    assert ProjectStore(path).get("u", "fe") == "hsk-app"


def test_project_md_round_trip_reads_only_frontmatter(tmp_path: Path) -> None:
    d = tmp_path / "proj"
    write_project_md(d, "proj", "a short desc")
    assert read_project_description(d) == "a short desc"
    text = (d / "PROJECT.md").read_text()
    assert text.startswith("---\n") and "## Rules & notes" in text  # frontmatter + body
    # never clobbers an existing one (the soul owns the body)
    write_project_md(d, "proj", "DIFFERENT")
    assert read_project_description(d) == "a short desc"


def test_existing_projects_returns_slug_and_description(tmp_path: Path) -> None:
    base = tmp_path / "fe" / "workspace"
    (base / "todo").mkdir(parents=True)  # no PROJECT.md → empty description
    write_project_md(base / "chinese-flashcard-style", "chinese-flashcard-style", "HSK app")
    (base / "_archive").mkdir()  # excluded
    (base / "note.txt").write_text("x")  # a file, not a project

    assert _existing_projects(tmp_path, "fe") == [
        ("chinese-flashcard-style", "HSK app"),
        ("todo", ""),
    ]
