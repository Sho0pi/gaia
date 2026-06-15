"""consult_soul: depth cap + cycle guard (no LLM) and the happy path (FakeLlm)."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from gaia import constants
from gaia.agents import AgentSpec
from gaia.config import Settings
from gaia.core import Gaia
from gaia.souls.consult import make_consult_soul
from gaia.souls.smith import SoulDecision


class FakeLlm(BaseLlm):
    model: str = "fake-model"
    responses: list[LlmResponse]

    async def generate_content_async(
        self, llm_request: Any, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
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
    monkeypatch.setattr("gaia.models.resolve_model", lambda *a, **k: fake)
    monkeypatch.setattr("gaia.agents.factory.resolve_model", lambda *a, **k: fake)


def _ctx(state: dict[str, Any]) -> Any:
    return SimpleNamespace(user_id="itay", state=state)


async def test_depth_cap_refuses(gaia: Gaia) -> None:
    # At the cap (default 2) consult refuses before any model call.
    consult = make_consult_soul(gaia)
    out = await consult("anything?", tool_context=_ctx({"consult_depth": 2}))
    assert out["status"] == "error" and "depth" in out["error_message"]


async def test_cycle_guard_refuses(gaia: Gaia, monkeypatch: pytest.MonkeyPatch) -> None:
    # The smith picks 'writer', already on the consult stack → refuse (A→B→A).
    reuse = SoulDecision(action="reuse", reason="fits", soul_key="writer")
    _install(monkeypatch, FakeLlm(responses=[_text(reuse.model_dump_json())]))
    consult = make_consult_soul(gaia)

    out = await consult("q", tool_context=_ctx({"consult_chain": ["writer"]}))

    assert out["status"] == "error" and "cycle" in out["error_message"]


async def test_happy_path_returns_answer(gaia: Gaia, monkeypatch: pytest.MonkeyPatch) -> None:
    forge = SoulDecision(
        action="forge",
        reason="need a nutritionist",
        spec=AgentSpec(
            name="Nutritionist", description="diet advice", instruction="Advise.", model="fake"
        ),
    )
    # 1st model call: the smith's decision. 2nd: the consulted soul's answer.
    _install(monkeypatch, FakeLlm(responses=[_text(forge.model_dump_json()), _text("~180g/day")]))
    consult = make_consult_soul(gaia)

    out = await consult("protein target for cutting?", tool_context=_ctx({}))

    assert out["status"] == "success"
    assert out["soul"] == "Nutritionist" and out["answer"] == "~180g/day"
