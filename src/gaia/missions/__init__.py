"""Missions — the durable task board (multi-agent orchestration substrate).

P1 ships the store; tools (`gaia.tools.task`), the `/task` command and the `gaia task`
CLI sit on top. See ``docs/missions-design.md`` for the full epic.
"""

from __future__ import annotations

from gaia.missions.store import A2A_STATE, CLOSED, Task, TaskStatus, TaskStore

__all__ = ["A2A_STATE", "CLOSED", "Task", "TaskStatus", "TaskStore"]
