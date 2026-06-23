"""Guard for #204: no store a test builds may point at the real ~/.gaia.

The autouse ``_isolate_home`` fixture (tests/conftest.py) redirects the home constants into a
per-test tmp dir. These assert the stores actually pick that up — so a test that builds a
``Gaia`` can never write the operator's live tasks.db / mem0 chroma / whatsapp session.
"""

from __future__ import annotations

from pathlib import Path

from gaia import constants
from gaia.config import Settings
from gaia.config.schema import MemoryConfig
from gaia.memory.backend import build_mem0_config
from gaia.missions import TaskStore

_REAL_HOME = Path.home() / f".{constants.APP_NAME}"


def _under_real_home(path: Path) -> bool:
    return _REAL_HOME == path or _REAL_HOME in path.parents


def test_task_store_default_is_not_the_real_home() -> None:
    assert not _under_real_home(TaskStore()._path)  # reads constants.TASKS_DB at construction


def test_settings_paths_are_not_the_real_home() -> None:
    s = Settings()  # default_factory reads the (tmp-redirected) constants
    assert not _under_real_home(s.whatsapp_session_db)
    assert not _under_real_home(s.log_dir)
    assert not _under_real_home(s.users_file)
    assert not _under_real_home(s.agent_registry_dir)


def test_mem0_chroma_path_is_not_the_real_home() -> None:
    cfg = build_mem0_config(Settings(), MemoryConfig())
    path = Path(cfg["vector_store"]["config"]["path"])
    assert not _under_real_home(path)  # mem0 wrote fake facts into real chroma before #204
