"""Filesystem tools: sandbox escape rejection, deny-list, per-agent isolation, fs_* behaviour."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from gaia.tools.fs import (
    Sandbox,
    SandboxError,
    make_fs_edit,
    make_fs_glob,
    make_fs_grep,
    make_fs_read,
    make_fs_write,
)


class _Ctx:
    """Stub of ADK's ToolContext — only ``agent_name`` is read by the tools."""

    def __init__(self, agent_name: str = "tester") -> None:
        self.agent_name = agent_name


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """The 'tester' agent's workspace under an isolated agents_dir."""
    return tmp_path / "tester" / "workspace"


# --- sandbox / escape ---------------------------------------------------------------


def test_relative_path_escape_rejected(tmp_path: Path) -> None:
    out = make_fs_read(tmp_path)("../../etc/passwd", tool_context=_Ctx())

    assert out["status"] == "error"
    assert "escapes" in out["error_message"]


def test_absolute_path_outside_roots_rejected(tmp_path: Path) -> None:
    out = make_fs_read(tmp_path)("/etc/hosts", tool_context=_Ctx())

    assert out["status"] == "error"
    assert "escapes" in out["error_message"]


def test_symlink_escape_rejected(tmp_path: Path, workspace: Path) -> None:
    workspace.mkdir(parents=True)
    secret = tmp_path / "outside.txt"
    secret.write_text("secret")
    (workspace / "link").symlink_to(secret)

    out = make_fs_read(tmp_path)("link", tool_context=_Ctx())

    assert out["status"] == "error"
    assert "escapes" in out["error_message"]


def test_scoped_tmp_is_allowed(tmp_path: Path) -> None:
    # The agent's scoped scratch dir (/tmp/gaia/<agent>) is a second allowed root.
    target = Path("/tmp/gaia/tester") / "scratch.txt"
    try:
        make_fs_write(tmp_path)(str(target), "hi", mode="overwrite", tool_context=_Ctx())
        out = make_fs_read(tmp_path)(str(target), tool_context=_Ctx())
        assert out["status"] == "success"
        assert out["content"] == "hi"
    finally:
        target.unlink(missing_ok=True)


def test_uploads_dir_is_readable_by_any_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A user's uploaded file (e.g. an inbound image) lives in the shared uploads dir, which is
    # a root in every agent's sandbox — so a tool/soul can read or copy it.
    uploads = tmp_path / "uploads"
    monkeypatch.setattr("gaia.constants.UPLOADS_DIR", uploads)
    uploads.mkdir()
    (uploads / "photo.png").write_text("img-bytes")

    out = make_fs_read(tmp_path)(str(uploads / "photo.png"), tool_context=_Ctx())

    assert out["status"] == "success" and out["content"] == "img-bytes"


def test_project_nests_the_workspace(tmp_path: Path) -> None:
    # A soul run sets current_project; every fs tool then anchors under workspace/<project>,
    # so two projects don't see each other's files (no overwrite).
    from gaia.tools.fs.base import current_project

    token = current_project.set("plant-shop")
    try:
        make_fs_write(tmp_path)("index.html", "shop", tool_context=_Ctx())
    finally:
        current_project.reset(token)

    assert (tmp_path / "tester" / "workspace" / "plant-shop" / "index.html").read_text() == "shop"

    # A different project can't see the first project's file (separate dirs).
    token = current_project.set("bakery")
    try:
        out = make_fs_read(tmp_path)("index.html", tool_context=_Ctx())
    finally:
        current_project.reset(token)
    assert out["status"] == "error" and "not a file" in out["error_message"]

    # Unset project (root agent / no run) -> flat workspace, also doesn't see the project file.
    assert make_fs_read(tmp_path)("index.html", tool_context=_Ctx())["status"] == "error"


def test_generic_tmp_is_blocked(tmp_path: Path) -> None:
    # A /tmp path outside the agent's own scratch dir must be refused.
    out = make_fs_read(tmp_path)("/tmp/other_app_secret.txt", tool_context=_Ctx())

    assert out["status"] == "error"
    assert "escapes" in out["error_message"]


def test_null_byte_rejected(tmp_path: Path) -> None:
    with pytest.raises(SandboxError, match="null byte"):
        Sandbox(tmp_path / "ws").resolve("a\x00b")


# --- deny-list / binary -------------------------------------------------------------


@pytest.mark.parametrize("name", [".env", "server.pem", "deploy.key", "id_rsa"])
def test_denied_files_not_read(tmp_path: Path, workspace: Path, name: str) -> None:
    workspace.mkdir(parents=True)
    (workspace / name).write_text("SECRET=1")

    out = make_fs_read(tmp_path)(name, tool_context=_Ctx())

    assert out["status"] == "error"
    assert "not allowed" in out["error_message"]


def test_binary_file_skipped(tmp_path: Path, workspace: Path) -> None:
    workspace.mkdir(parents=True)
    (workspace / "blob.dat").write_bytes(b"abc\x00\x01\x02def")

    out = make_fs_read(tmp_path)("blob.dat", tool_context=_Ctx())

    assert out["status"] == "error"
    assert "binary" in out["error_message"]


# --- fs_read ------------------------------------------------------------------------


def test_read_line_range_and_numbers(tmp_path: Path, workspace: Path) -> None:
    workspace.mkdir(parents=True)
    (workspace / "f.txt").write_text("a\nb\nc\nd\n")

    out = make_fs_read(tmp_path)(
        "f.txt", start_line=2, end_line=3, include_line_numbers=True, tool_context=_Ctx()
    )

    assert out["status"] == "success"
    assert out["content"] == "2\tb\n3\tc"
    assert out["total_lines"] == 4
    assert out["truncated"] is False


def test_read_max_bytes_truncates(tmp_path: Path, workspace: Path) -> None:
    workspace.mkdir(parents=True)
    (workspace / "big.txt").write_text("x" * 100)

    out = make_fs_read(tmp_path)("big.txt", max_bytes=10, tool_context=_Ctx())

    assert out["truncated"] is True
    assert len(out["content"]) == 10


# --- fs_write -----------------------------------------------------------------------


def test_write_create_then_fails_if_exists(tmp_path: Path) -> None:
    write = make_fs_write(tmp_path)

    first = write("note.txt", "one", tool_context=_Ctx())
    second = write("note.txt", "two", tool_context=_Ctx())

    assert first["status"] == "success"
    assert first["created"] is True
    assert second["status"] == "error"
    assert "exists" in second["error_message"]


def test_write_append_and_overwrite(tmp_path: Path, workspace: Path) -> None:
    write = make_fs_write(tmp_path)
    write("log.txt", "a", tool_context=_Ctx())
    write("log.txt", "b", mode="append", tool_context=_Ctx())

    assert (workspace / "log.txt").read_text() == "ab"

    write("log.txt", "z", mode="overwrite", tool_context=_Ctx())
    assert (workspace / "log.txt").read_text() == "z"


def test_write_create_dirs_and_backup(tmp_path: Path, workspace: Path) -> None:
    write = make_fs_write(tmp_path)
    write("sub/dir/x.txt", "v1", create_dirs=True, tool_context=_Ctx())
    write("sub/dir/x.txt", "v2", mode="overwrite", backup=True, tool_context=_Ctx())

    assert (workspace / "sub/dir/x.txt").read_text() == "v2"
    assert (workspace / "sub/dir/x.txt.bak").read_text() == "v1"


def test_write_missing_parent_without_create_dirs(tmp_path: Path) -> None:
    out = make_fs_write(tmp_path)("no/where.txt", "v", tool_context=_Ctx())

    assert out["status"] == "error"
    assert "parent directory" in out["error_message"]


# --- fs_edit ------------------------------------------------------------------------


def test_edit_replaces_unique(tmp_path: Path, workspace: Path) -> None:
    workspace.mkdir(parents=True)
    (workspace / "f.txt").write_text("hello world")

    out = make_fs_edit(tmp_path)("f.txt", "world", "there", tool_context=_Ctx())

    assert out == {"status": "success", "replacements": 1, "dry_run": False}
    assert (workspace / "f.txt").read_text() == "hello there"


def test_edit_count_mismatch_refused(tmp_path: Path, workspace: Path) -> None:
    workspace.mkdir(parents=True)
    (workspace / "f.txt").write_text("a a a")

    out = make_fs_edit(tmp_path)("f.txt", "a", "b", tool_context=_Ctx())  # expects 1, finds 3

    assert out["status"] == "error"
    assert "matched 3" in out["error_message"]
    assert (workspace / "f.txt").read_text() == "a a a"  # untouched


def test_edit_dry_run_does_not_write(tmp_path: Path, workspace: Path) -> None:
    workspace.mkdir(parents=True)
    (workspace / "f.txt").write_text("one two")

    out = make_fs_edit(tmp_path)("f.txt", "two", "three", dry_run=True, tool_context=_Ctx())

    assert out == {"status": "success", "replacements": 1, "dry_run": True}
    assert (workspace / "f.txt").read_text() == "one two"  # unchanged


def test_edit_backup_created(tmp_path: Path, workspace: Path) -> None:
    workspace.mkdir(parents=True)
    (workspace / "f.txt").write_text("keep me")

    make_fs_edit(tmp_path)("f.txt", "keep", "drop", backup=True, tool_context=_Ctx())

    assert (workspace / "f.txt.bak").read_text() == "keep me"


# --- per-agent isolation + logging --------------------------------------------------


def test_agents_get_separate_workspaces(tmp_path: Path) -> None:
    write = make_fs_write(tmp_path)
    write("a.txt", "from-alice", tool_context=_Ctx("alice"))

    bob_read = make_fs_read(tmp_path)("a.txt", tool_context=_Ctx("bob"))

    assert bob_read["status"] == "error"  # bob has his own empty workspace
    assert (tmp_path / "alice" / "workspace" / "a.txt").exists()
    assert not (tmp_path / "bob" / "workspace" / "a.txt").exists()


# --- hierarchical access (issue #121): root sees all, souls only their own -----------


def test_root_agent_reads_a_souls_workspace(tmp_path: Path) -> None:
    # A soul writes a deliverable; the root agent ("gaia") opens it via the absolute
    # path delegate_to_soul reports — the whole point of the hierarchical model.
    make_fs_write(tmp_path)("index.html", "<h1>hi</h1>", tool_context=_Ctx("web_designer"))
    deliverable = tmp_path / "web_designer" / "workspace" / "index.html"

    out = make_fs_read(tmp_path)(str(deliverable), tool_context=_Ctx("gaia"))

    assert out["status"] == "success"
    assert "<h1>hi</h1>" in out["content"]


def test_root_agent_cannot_write_into_a_souls_workspace(tmp_path: Path) -> None:
    # Read-only over the tree: the root reads/relays deliverables but must not edit a soul's
    # work — changing it is the soul's job (re-delegate). Stops Gaia overstepping a worker.
    make_fs_write(tmp_path)("index.html", "<h1>hi</h1>", tool_context=_Ctx("web_designer"))
    target = tmp_path / "web_designer" / "workspace" / "index.html"

    out = make_fs_write(tmp_path)(str(target), "hacked", tool_context=_Ctx("gaia"))

    assert out["status"] == "error"
    assert "re-delegate" in out["error_message"]  # message steers the model to the soul
    assert target.read_text() == "<h1>hi</h1>"  # untouched


def test_root_agent_writes_its_own_workspace(tmp_path: Path) -> None:
    # The boundary is other agents' trees only — the root keeps full write on its own scratch
    # (it needs this to e.g. zip files for send_file).
    out = make_fs_write(tmp_path)("scratch.txt", "mine", tool_context=_Ctx("gaia"))

    assert out["status"] == "success"
    assert (tmp_path / "gaia" / "workspace" / "scratch.txt").read_text() == "mine"


def test_root_agent_cannot_edit_a_souls_file(tmp_path: Path) -> None:
    make_fs_write(tmp_path)("a.txt", "light theme", tool_context=_Ctx("web_designer"))
    target = tmp_path / "web_designer" / "workspace" / "a.txt"

    out = make_fs_edit(tmp_path)(
        str(target), old_string="light theme", new_string="dark theme", tool_context=_Ctx("gaia")
    )

    assert out["status"] == "error" and "re-delegate" in out["error_message"]
    assert target.read_text() == "light theme"


def test_soul_cannot_read_sibling_or_agents_root(tmp_path: Path) -> None:
    make_fs_write(tmp_path)("a.txt", "secret", tool_context=_Ctx("alice"))
    sibling = tmp_path / "alice" / "workspace" / "a.txt"

    via_path = make_fs_read(tmp_path)(str(sibling), tool_context=_Ctx("bob"))
    via_root = make_fs_read(tmp_path)(str(tmp_path), tool_context=_Ctx("bob"))

    assert via_path["status"] == "error"  # a soul stays sealed in its own workspace
    assert via_root["status"] == "error"


def test_root_agent_still_cannot_leave_the_agents_tree(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"

    out = make_fs_read(tmp_path)(str(outside), tool_context=_Ctx("gaia"))

    assert out["status"] == "error"  # wider view is the agents tree, not the host


def test_root_agent_deny_list_still_applies(tmp_path: Path) -> None:
    # Hierarchical access must not weaken the secrets deny-list.
    env = tmp_path / "web_designer" / "workspace" / ".env"
    env.parent.mkdir(parents=True)
    env.write_text("API_KEY=x")

    out = make_fs_read(tmp_path)(str(env), tool_context=_Ctx("gaia"))

    assert out["status"] == "error"


# --- fs_glob / fs_grep (need fd / rg) -----------------------------------------------


@pytest.mark.skipif(shutil.which("fd") is None, reason="needs the fd binary")
def test_glob_matches_and_truncates(tmp_path: Path, workspace: Path) -> None:
    workspace.mkdir(parents=True)
    for i in range(5):
        (workspace / f"file{i}.py").write_text("x")
    (workspace / "note.txt").write_text("x")

    out = make_fs_glob(tmp_path)("**/*.py", tool_context=_Ctx())
    assert out["status"] == "success"
    assert out["count"] == 5
    assert all(m.endswith(".py") for m in out["matches"])

    capped = make_fs_glob(tmp_path)("**/*.py", max_results=2, tool_context=_Ctx())
    assert capped["count"] == 2
    assert capped["truncated"] is True


@pytest.mark.skipif(shutil.which("rg") is None, reason="needs the rg binary")
def test_grep_match_and_context_format(tmp_path: Path, workspace: Path) -> None:
    workspace.mkdir(parents=True)
    (workspace / "code.py").write_text("import os\nNEEDLE = 1\nprint(NEEDLE)\n")

    out = make_fs_grep(tmp_path)("NEEDLE", context_lines=1, tool_context=_Ctx())

    assert out["status"] == "success"
    assert any("code.py:2:" in m for m in out["matches"])  # match uses colon
    assert any("code.py-1-" in m for m in out["matches"])  # context uses dash


@pytest.mark.skipif(shutil.which("rg") is None, reason="needs the rg binary")
def test_grep_glob_filter(tmp_path: Path, workspace: Path) -> None:
    workspace.mkdir(parents=True)
    (workspace / "a.py").write_text("TARGET\n")
    (workspace / "b.txt").write_text("TARGET\n")

    out = make_fs_grep(tmp_path)("TARGET", glob="*.py", tool_context=_Ctx())

    assert out["count"] == 1
    assert "a.py" in out["matches"][0]
