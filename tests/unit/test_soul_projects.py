"""Project resolution: converge a soul's app on one workspace instead of forking per turn."""

from __future__ import annotations

from pathlib import Path

from gaia.souls.projects import ProjectStore
from gaia.souls.run import _existing_projects, resolve_project


def _store(tmp_path: Path) -> ProjectStore:
    return ProjectStore(tmp_path / "projects.json")


def test_named_exact_match_reuses(tmp_path: Path) -> None:
    got = resolve_project("hsk-app", "tweak it", "u", "fe", ["hsk-app", "todo"], _store(tmp_path))
    assert got == "hsk-app"


def test_sentence_naming_an_existing_project_reuses_it(tmp_path: Path) -> None:
    # The model passes a whole sentence as `project` — it names hsk-app, so continue it (no fork).
    got = resolve_project(
        "extend the hsk-app project into a polished site",
        "...",
        "u",
        "fe",
        ["hsk-app"],
        _store(tmp_path),
    )
    assert got == "hsk-app"


def test_genuinely_new_name_starts_a_new_dir(tmp_path: Path) -> None:
    got = resolve_project("todo", "build a todo app", "u", "fe", ["hsk-app"], _store(tmp_path))
    assert got == "todo"


def test_omitted_continues_the_last_project(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set("u", "fe", "hsk-app")  # what the soul last worked on
    got = resolve_project("", "make the header bigger", "u", "fe", ["hsk-app", "todo"], store)
    assert got == "hsk-app"  # not a fresh fork


def test_omitted_with_no_history_is_a_fresh_slug(tmp_path: Path) -> None:
    store = _store(tmp_path)
    got = resolve_project("", "build a landing page", "u", "fe", ["hsk-app"], store)
    assert got.startswith("build_a_landing_page") and got != "hsk-app"
    assert store.get("u", "fe") == got  # recorded as the new current project


def test_resolution_is_recorded_so_next_omit_continues_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    resolve_project("todo", "x", "u", "fe", ["hsk-app"], store)
    assert store.get("u", "fe") == "todo"


def test_store_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "projects.json"
    ProjectStore(path).set("u", "fe", "hsk-app")
    assert ProjectStore(path).get("u", "fe") == "hsk-app"  # survives /reset + restart


def test_existing_projects_lists_dirs_and_skips_archive(tmp_path: Path) -> None:
    base = tmp_path / "fe" / "workspace"
    (base / "hsk-app").mkdir(parents=True)
    (base / "todo").mkdir()
    (base / "_archive").mkdir()  # underscore-prefixed → excluded
    (base / "note.txt").write_text("x")  # a file, not a project
    assert _existing_projects(tmp_path, "fe") == ["hsk-app", "todo"]
