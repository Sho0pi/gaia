"""souls.run: the tool-context-free smith path + the shared execute core (FakeLlm)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from _fakes import FakeLlm
from _fakes import text_response as _text
from gaia import constants
from gaia.agents import AgentSpec
from gaia.core import Gaia
from gaia.souls.run import _AgentTurn, decide_soul, execute_decision, run_soul_agent
from gaia.souls.smith import SoulDecision


@pytest.fixture
async def gaia(make_gaia: Any) -> Gaia:
    return make_gaia()  # isolated, memory off, closed on teardown (tests use constants.AGENTS_DIR)


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


async def test_execute_decision_copies_a_sent_attachment(
    gaia: Gaia, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A file handed with the delegation (another soul's deliverable, under the agents tree) is
    # copied into the target soul's workspace as a relative file — the agent-to-agent handoff.
    _install(monkeypatch, FakeLlm(responses=[_text("done")]))
    src = constants.AGENTS_DIR / "gym_bro" / "workspace" / "plan.pdf"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"%PDF plan")

    run = await execute_decision(gaia, _FORGE, "build a site", user_id="i", attachments=[str(src)])

    assert run.ok
    dest = Path(run.workspace) / "plan.pdf"
    assert dest.read_bytes() == b"%PDF plan"
    assert "plan.pdf" not in run.files  # copied pre-snapshot, not a deliverable of this run


async def test_execute_decision_rejects_attachment_outside_the_tree(
    gaia: Gaia, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The trust boundary: a path outside the agents/uploads tree is not pulled into a workspace.
    _install(monkeypatch, FakeLlm(responses=[_text("done")]))
    outside = tmp_path / "host-secret.txt"
    outside.write_text("nope")

    run = await execute_decision(gaia, _FORGE, "x", user_id="i", attachments=[str(outside)])

    assert run.ok
    assert not (Path(run.workspace) / "host-secret.txt").exists()


async def test_execute_decision_rejects_denied_attachment(
    gaia: Gaia, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Even inside the tree, a secret/denied file (.env) is refused.
    _install(monkeypatch, FakeLlm(responses=[_text("done")]))
    env = constants.AGENTS_DIR / "gym_bro" / "workspace" / ".env"
    env.parent.mkdir(parents=True)
    env.write_text("API_KEY=x")

    run = await execute_decision(gaia, _FORGE, "x", user_id="i", attachments=[str(env)])

    assert run.ok
    assert not (Path(run.workspace) / ".env").exists()


async def test_same_project_reuses_a_warm_session(
    gaia: Gaia, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Re-delegating to the same (soul, project) resumes one warm session — the soul keeps its
    # history (so it doesn't re-read the workspace). A different project gets its own session.
    _install(monkeypatch, FakeLlm(responses=[_text("a"), _text("b"), _text("c")]))

    await execute_decision(gaia, _FORGE, "build", user_id="i", project="shop")
    first = gaia.soul_sessions._sessions["writer/shop"]
    events_after_1 = len(
        (
            await first.session_service.get_session(  # type: ignore[union-attr]
                app_name=constants.APP_NAME, user_id="i", session_id=first.session_id
            )
        ).events
    )

    await execute_decision(gaia, _FORGE, "edit it", user_id="i", project="shop")
    second = gaia.soul_sessions._sessions["writer/shop"]
    events_after_2 = len(
        (
            await second.session_service.get_session(  # type: ignore[union-attr]
                app_name=constants.APP_NAME, user_id="i", session_id=second.session_id
            )
        ).events
    )

    assert second is first  # same warm session reused, not rebuilt
    assert events_after_2 > events_after_1  # the second turn appended to the same history

    await execute_decision(gaia, _FORGE, "other", user_id="i", project="bakery")
    assert set(gaia.soul_sessions._sessions) == {"writer/shop", "writer/bakery"}


async def test_smith_path_is_not_warmed(gaia: Gaia, monkeypatch: pytest.MonkeyPatch) -> None:
    # decide_soul runs the smith via run_soul_agent with no warm_key — a one-shot, never cached.
    _install(monkeypatch, FakeLlm(responses=[_text(_FORGE.model_dump_json())]))

    await decide_soul(gaia, "write me a poem")

    assert gaia.soul_sessions._sessions == {}  # the smith left no warm session


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
        g: Any,
        soul: Any,
        key: str,
        task: str,
        user_id: str,
        *,
        state: Any = None,
        warm_key: Any = None,
    ) -> _AgentTurn:
        seen["state"] = state
        return _AgentTurn("done", [])

    monkeypatch.setattr("gaia.souls.run.run_soul_agent", spy)

    run = await execute_decision(
        gaia, _FORGE, "write", user_id="itay", state={"task_id": "t1", "owner": "itay"}
    )

    assert run.ok
    assert seen["state"]["task_id"] == "t1"
    assert seen["state"]["created_by"] == "writer"  # stamped with the soul's own key


def test_deliverable_media_includes_artifacts_excludes_source(tmp_path: Path) -> None:
    from gaia.souls.run import _deliverable_media

    primary = tmp_path / "ws"
    primary.mkdir()
    names = ["report.pdf", "plan.docx", "data.xlsx", "bundle.zip", "shot.png", "clip.mp4"]
    source = ["index.html", "style.css", "app.js", "manifest.json"]
    for n in (*names, *source):
        (primary / n).write_text("x")

    out = _deliverable_media(primary, [*names, *source], run_media=["/tmp/preview.png"])

    got = {Path(p).name for p in out}
    assert got == {*names, "preview.png"}  # artifacts + the screenshot; no web source
    assert out[0] == "/tmp/preview.png"  # run_media first, order-stable


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
    turn = await run_soul_agent(gaia, soul, "writer", "do it", user_id="i")

    assert turn.text == "done" and turn.media == []
    assert closed == []  # shared toolset survived — bug 4 regression guard


async def test_resume_soul_feeds_answer_and_finishes(
    gaia: Gaia, monkeypatch: pytest.MonkeyPatch
) -> None:
    # resume_soul re-enters the soul session with the answer as the ask_user function-response,
    # skipping the smith, and returns a finished SoulRun.
    from gaia.core.elicit import SoulPending
    from gaia.souls import run as run_mod

    _install(monkeypatch, FakeLlm(responses=[]))  # soul built but not run (_run_soul_content faked)
    gaia.souls.save(_FORGE.spec)
    captured: dict[str, Any] = {}

    async def fake_content(
        g: Any,
        soul: Any,
        key: str,
        user_id: str,
        content: Any,
        *,
        state: Any = None,
        warm_key: Any = None,
    ) -> _AgentTurn:
        captured["content"] = content
        captured["warm_key"] = warm_key
        return _AgentTurn("ready with sk-9", [], paused=None)

    monkeypatch.setattr(run_mod, "_run_soul_content", fake_content)
    pending = SoulPending(
        warm_key=f"{_FORGE.spec.key}/p",
        soul_key=_FORGE.spec.key,
        project="p",
        soul_fc_id="sfc",
        question="key?",
        user_id="u",
        before={},
    )

    out = await run_mod.resume_soul(gaia, pending, "sk-9")

    assert out.ok and out.summary == "ready with sk-9"
    fr = captured["content"].parts[0].function_response
    assert fr.id == "sfc" and fr.name == "ask_user" and fr.response == {"answer": "sk-9"}
    assert captured["warm_key"] == f"{_FORGE.spec.key}/p"  # the same warm session is re-entered


async def test_resume_soul_can_pause_again(gaia: Gaia, monkeypatch: pytest.MonkeyPatch) -> None:
    from gaia.core.elicit import SoulPending
    from gaia.souls import run as run_mod

    _install(monkeypatch, FakeLlm(responses=[]))
    gaia.souls.save(_FORGE.spec)

    async def fake_content(*_a: Any, **_k: Any) -> _AgentTurn:
        call = SimpleNamespace(id="sfc2", args={"question": "anything else?", "options": None})
        return _AgentTurn("", [], paused=call)

    monkeypatch.setattr(run_mod, "_run_soul_content", fake_content)
    before = {"index.html": 1.0}
    pending = SoulPending(
        warm_key=f"{_FORGE.spec.key}/p",
        soul_key=_FORGE.spec.key,
        project="p",
        soul_fc_id="sfc",
        question="key?",
        user_id="u",
        before=before,
    )

    out = await run_mod.resume_soul(gaia, pending, "sk-9")

    assert out.pending is not None and out.pending.soul_fc_id == "sfc2"
    assert out.pending.before is before  # the baseline is carried forward for a cumulative diff


async def test_decide_soul_roundtrips_through_json(
    gaia: Gaia, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Belt-and-suspenders: the smith's JSON output validates back to a SoulDecision.
    raw = _FORGE.model_dump_json()
    assert SoulDecision.model_validate(json.loads(raw)).action == "forge"
