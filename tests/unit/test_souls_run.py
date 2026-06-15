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
from gaia.souls.run import decide_soul, execute_decision
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
    ) -> str:
        seen["state"] = state
        return "done"

    monkeypatch.setattr("gaia.souls.run.run_soul_agent", spy)

    run = await execute_decision(
        gaia, _FORGE, "write", user_id="itay", state={"task_id": "t1", "owner": "itay"}
    )

    assert run.ok
    assert seen["state"]["task_id"] == "t1"
    assert seen["state"]["created_by"] == "writer"  # stamped with the soul's own key


async def test_decide_soul_roundtrips_through_json(
    gaia: Gaia, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Belt-and-suspenders: the smith's JSON output validates back to a SoulDecision.
    raw = _FORGE.model_dump_json()
    assert SoulDecision.model_validate(json.loads(raw)).action == "forge"
