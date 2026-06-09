"""Filesystem tools: sandbox escape rejection, deny-list, per-agent isolation, fs_* behaviour."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from godpy.tools.fs import (
    Sandbox,
    SandboxError,
    make_fs_edit,
    make_fs_glob,
    make_fs_grep,
    make_fs_read,
    make_fs_write,
)
from godpy.tools.fs import write as fs_write_mod


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
    # The agent's scoped scratch dir (/tmp/godpy/<agent>) is a second allowed root.
    target = Path("/tmp/godpy/tester") / "scratch.txt"
    try:
        make_fs_write(tmp_path)(str(target), "hi", mode="overwrite", tool_context=_Ctx())
        out = make_fs_read(tmp_path)(str(target), tool_context=_Ctx())
        assert out["status"] == "success"
        assert out["content"] == "hi"
    finally:
        target.unlink(missing_ok=True)


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


def test_tool_call_logged_success_and_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(fs_write_mod, "log_event", lambda action, **f: events.append((action, f)))
    write = make_fs_write(tmp_path)

    write("ok.txt", "v", tool_context=_Ctx())
    write("../escape", "v", tool_context=_Ctx())

    assert [e[1]["status"] for e in events] == ["success", "error"]
    assert all(e[0] == "tool_used" and e[1]["tool"] == "fs_write" for e in events)
    assert events[0][1]["agent"] == "tester"


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
