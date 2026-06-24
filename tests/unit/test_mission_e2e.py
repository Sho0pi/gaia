"""End-to-end mission through the real dispatcher with a scripted model (FakeLlm, no key).

The gym-site scenario the owner asked for: a multi-step mission where a *personal-trainer*
soul designs the program and a *frontend-designer* soul builds the site from it. Exercises
the whole P2 chain — task_plan DAG → dispatcher → smith (reuse/forge) → soul run → result
hand-off → leaf-only push — without spending real tokens.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import gaia.missions.dispatcher as disp_mod
from _fakes import FakeLlm, forge_response
from _fakes import FakeSender as _Sender
from _fakes import text_response as _text
from gaia import constants
from gaia.config import Settings
from gaia.core import Gaia
from gaia.missions import TaskStatus, TaskStore
from gaia.missions.dispatcher import MissionDispatcher
from gaia.tools.task import make_task_plan


def _ctx(user_id: str) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(user_id=user_id)  # ADK public ToolContext.user_id


@pytest.fixture
def gaia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Gaia:
    monkeypatch.setattr(constants, "AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(constants, "TASKS_DB", tmp_path / "tasks.db")
    config_path = tmp_path / "gaia.yaml"
    config_path.write_text("memory:\n  enabled: false\n")
    return Gaia(Settings(agent_registry_dir=tmp_path / "reg", config_path=config_path))


async def test_gym_site_mission_runs_two_souls_with_handoff(
    gaia: Gaia, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Scripted in dispatch order: program task (smith→forge PT, soul writes program.md),
    # then site task (smith→forge frontend, soul writes index.html using the program).
    fake = FakeLlm(
        responses=[
            forge_response("Personal Trainer"),
            _text("Wrote the A/B split to program.md"),
            forge_response("Frontend Designer"),
            _text("Built the site in index.html"),
        ]
    )
    monkeypatch.setattr("gaia.models.resolve_model", lambda *a, **k: fake)
    monkeypatch.setattr("gaia.agents.factory.resolve_model", lambda *a, **k: fake)

    wa = _Sender()
    gaia.connectors["whatsapp"] = wa
    store = TaskStore()

    # The leaf-task result is pushed to the owner's connector, so the owner must be a known
    # user with a whatsapp identity. Register them here so the test is self-contained rather
    # than relying on whatever happens to be in the real user store.
    owner = gaia.users.register("whatsapp", "itay@s.whatsapp.net", "Itay", role="admin").id

    # Capture each soul's input so we can prove the hand-off (T2 sees T1's result).
    inputs: list[str] = []
    real_execute = disp_mod.execute_decision

    async def spy_execute(
        g: Any,
        decision: Any,
        task: str,
        user: str,
        *,
        project: str = "",
        attachments: Any = None,
        state: Any = None,
    ) -> Any:
        inputs.append(task)
        return await real_execute(
            g, decision, task, user, project=project, attachments=attachments, state=state
        )

    monkeypatch.setattr(disp_mod, "execute_decision", spy_execute)

    plan = json.dumps(
        [
            {"ref": "program", "title": "Design A/B gym program", "spec": "Design an A/B split."},
            {
                "ref": "site",
                "title": "Build the workout site",
                "spec": "Build a site for the program.",
                "depends_on": ["program"],
            },
        ]
    )
    make_task_plan(store)(plan, tool_context=_ctx(owner))

    d = MissionDispatcher(gaia, store=store, poll_seconds=0.01)
    async with gaia:
        d.start()
        for _ in range(200):  # poll until both done (or give up)
            if all(t.status is TaskStatus.DONE for t in store.list()):
                break
            await asyncio.sleep(0.02)
        await d.stop()

    tasks = {t.title: t for t in store.list()}
    assert tasks["Design A/B gym program"].status is TaskStatus.DONE
    assert tasks["Build the workout site"].status is TaskStatus.DONE

    # Hand-off: the site task's soul input carried the program task's result.
    site_input = next(i for i in inputs if "Build a site" in i)
    assert "program.md" in site_input  # the upstream result text was injected

    # Two distinct, reusably-named souls were forged (not one task-specific blob).
    souls = set(gaia.souls.list_keys())
    assert "personal_trainer" in souls and "frontend_designer" in souls

    # Leaf-only delivery: exactly one push (the site), not the internal program step.
    assert len(wa.sent) == 1
