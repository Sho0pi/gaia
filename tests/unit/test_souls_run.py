"""souls.run: the tool-context-free smith path + the shared execute core (FakeLlm)."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from gaia import constants
from gaia.agents import AgentSpec
from gaia.config import Settings
from gaia.core import Gaia
from gaia.souls.run import decide_soul, execute_decision, run_soul_agent
from gaia.souls.smith import SoulDecision


class FakeLlm(BaseLlm):
    """Scripted model: yields one canned response per generate call, in order."""

    model: str = "fake-model"
    responses: list[LlmResponse]
    calls: int = 0

    async def generate_content_async(
        self, llm_request: Any, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.calls += 1
        yield self.responses.pop(0)


def _text(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


@pytest.fixture
def gaia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Gaia:
    monkeypatch.setattr(constants, "AGENTS_DIR", tmp_path / "agents")
    config_path = tmp_path / "gaia.yaml"
    config_path.write_text("memory:\n  enabled: false\n")
    return Gaia(Settings(agent_registry_dir=tmp_path / "reg", config_path=config_path))


def _install(monkeypatch: pytest.MonkeyPatch, fake: FakeLlm) -> None:
    # The smith resolves its model via gaia.models; the soul agent via the factory's import.
    monkeypatch.setattr("gaia.models.resolve_model", lambda *a, **k: fake)
    monkeypatch.setattr("gaia.agents.factory.resolve_model", lambda *a, **k: fake)


_FORGE = SoulDecision(
    action="forge",
    reason="no soul fits",
    spec=AgentSpec(name="Writer", description="writes things", instruction="Write.", model="fake"),
)


async def test_decide_soul_parses_decision_from_a_runner(
    gaia: Gaia, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, FakeLlm(responses=[_text(_FORGE.model_dump_json())]))

    decision = await decide_soul(gaia, "write me a poem")

    assert decision.action == "forge"
    assert decision.spec is not None and decision.spec.name == "Writer"


async def test_execute_decision_forge_runs_the_soul(
    gaia: Gaia, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, FakeLlm(responses=[_text("wrote the poem")]))

    run = await execute_decision(gaia, _FORGE, "write me a poem", user_id="itay")

    assert run.ok and run.created and run.soul_name == "Writer"
    assert run.summary == "wrote the poem"
    assert gaia.souls.get("writer") is not None  # forged soul persisted for reuse


async def test_execute_decision_copies_attachments_into_workspace(
    gaia: Gaia, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A user's uploaded image must land in the soul's workspace (relative, servable) — not stay
    # in the shared uploads dir where a served site can't reach it.
    from gaia.connectors.base import inbound_attachments

    upload = tmp_path / "logo.png"
    upload.write_bytes(b"img-bytes")
    _install(monkeypatch, FakeLlm(responses=[_text("built the site")]))

    token = inbound_attachments.set((upload,))
    try:
        run = await execute_decision(gaia, _FORGE, "put the logo on a page", user_id="itay")
    finally:
        inbound_attachments.reset(token)

    assert run.ok
    assert (Path(run.workspace) / "logo.png").read_bytes() == b"img-bytes"


async def test_execute_decision_scopes_runs_to_separate_project_dirs(
    gaia: Gaia, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two named projects -> two dirs (no overwrite); same name -> same dir (continue editing);
    # omitted -> a fresh unique dir each time.
    _install(monkeypatch, FakeLlm(responses=[_text("ok")] * 4))

    a = await execute_decision(gaia, _FORGE, "build site", user_id="i", project="plant-shop")
    b = await execute_decision(gaia, _FORGE, "build site", user_id="i", project="bakery")
    a2 = await execute_decision(gaia, _FORGE, "edit site", user_id="i", project="plant-shop")
    assert a.workspace.endswith("/plant-shop") and b.workspace.endswith("/bakery")
    assert a.workspace != b.workspace  # separate projects, separate dirs
    assert a2.workspace == a.workspace  # same slug reuses the project dir

    u1 = await execute_decision(gaia, _FORGE, "build site", user_id="i")
    u2 = await execute_decision(gaia, _FORGE, "build site", user_id="i")
    assert u1.workspace != u2.workspace  # omitted project -> unique each run


async def test_execute_decision_attachment_lands_in_the_project_dir(
    gaia: Gaia, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gaia.connectors.base import inbound_attachments

    upload = tmp_path / "logo.png"
    upload.write_bytes(b"img")
    _install(monkeypatch, FakeLlm(responses=[_text("ok")]))

    token = inbound_attachments.set((upload,))
    try:
        run = await execute_decision(gaia, _FORGE, "use logo", user_id="i", project="shop")
    finally:
        inbound_attachments.reset(token)

    assert run.workspace.endswith("/shop")
    assert (Path(run.workspace) / "logo.png").read_bytes() == b"img"


async def test_execute_decision_reuse_uses_stored_soul(
    gaia: Gaia, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaia.souls.save(
        AgentSpec(name="Writer", description="writes", instruction="Write.", model="fake")
    )
    _install(monkeypatch, FakeLlm(responses=[_text("reused output")]))

    reuse = SoulDecision(action="reuse", reason="fits", soul_key="writer")
    run = await execute_decision(gaia, reuse, "write", user_id="itay")

    assert run.ok and not run.created and run.summary == "reused output"


async def test_execute_decision_seeds_session_state(
    gaia: Gaia, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The dispatcher's task identity (task_id/owner) plus the soul's own key must reach the
    # soul's session state — the seam P3 tools read to file subtasks / bound consult depth.
    seen: dict[str, Any] = {}

    async def spy(
        g: Any, soul: Any, key: str, task: str, user_id: str, *, state: Any = None
    ) -> tuple[str, list[str]]:
        seen["state"] = state
        return "done", []

    monkeypatch.setattr("gaia.souls.run.run_soul_agent", spy)

    run = await execute_decision(
        gaia, _FORGE, "write", user_id="itay", state={"task_id": "t1", "owner": "itay"}
    )

    assert run.ok
    assert seen["state"]["task_id"] == "t1"
    assert seen["state"]["created_by"] == "writer"  # stamped with the soul's own key


async def test_run_soul_agent_never_closes_shared_toolsets(
    gaia: Gaia, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A soul's tools include the *shared* MCP/Skills toolset singletons (same objects on the
    # root). ADK's Runner.close() would close every toolset on the agent — so the nested soul
    # runner must NOT close, or it tears the root's browser/skills down mid-conversation and
    # the chat goes silent. Guard: a toolset on the soul survives the run.
    from google.adk.agents import LlmAgent
    from google.adk.tools.base_toolset import BaseToolset

    closed: list[bool] = []

    class TrackingToolset(BaseToolset):
        async def get_tools(self, readonly_context: Any = None) -> list[Any]:
            return []

        async def close(self) -> None:
            closed.append(True)

    soul = LlmAgent(
        name="writer", model=FakeLlm(responses=[_text("done")]), tools=[TrackingToolset()]
    )
    text, media = await run_soul_agent(gaia, soul, "writer", "do it", user_id="i")

    assert text == "done" and media == []
    assert closed == []  # shared toolset survived — bug 4 regression guard


async def test_decide_soul_roundtrips_through_json(
    gaia: Gaia, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Belt-and-suspenders: the smith's JSON output validates back to a SoulDecision.
    raw = _FORGE.model_dump_json()
    assert SoulDecision.model_validate(json.loads(raw)).action == "forge"
