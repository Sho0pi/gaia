"""``gaia soul`` group: manual CRUD (no model key), prompts, edit, and the --ai flow.

All offline: a tmp ``agent_registry`` wired through ``get_settings``; ``click.edit`` and
the soul-smith run (``soul._forge``) are monkeypatched so nothing opens an editor or
calls a model. Dummy values only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gaia.agents import AgentSpec, SoulRegistry
from gaia.cli import app as cli_app
from gaia.cli import soul
from gaia.config import Settings
from gaia.souls.smith import SoulDecision

runner = CliRunner()


@pytest.fixture
def registry_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp registry + config wired into the light ``get_settings`` the commands call."""
    reg = tmp_path / "agent_registry"
    settings = Settings(agent_registry_dir=reg, config_path=tmp_path / "gaia.yaml")
    monkeypatch.setattr("gaia.config.get_settings", lambda env_file=None: settings)
    return reg


def _spec(**over: object) -> AgentSpec:
    base = {
        "name": "Note Taker",
        "description": "Takes meeting notes.",
        "instruction": "You take notes.",
        "model": "gemini-2.0-flash",
    }
    return AgentSpec.model_validate({**base, **over})


# --- create (manual, no key) -------------------------------------------------------


def test_create_manual_roundtrip(registry_dir: Path) -> None:
    result = runner.invoke(
        cli_app,
        ["soul", "create", "Note Taker", "--description", "Takes notes", "--instruction", "Do it."],
    )

    assert result.exit_code == 0
    assert (registry_dir / "note_taker.md").exists()
    spec = SoulRegistry(registry_dir).get("note_taker")
    assert spec is not None and spec.instruction == "Do it."


def test_create_reads_instruction_file(registry_dir: Path, tmp_path: Path) -> None:
    f = tmp_path / "instr.md"
    f.write_text("From a file.")

    result = runner.invoke(
        cli_app,
        ["soul", "create", "Note Taker", "--description", "d", "--instruction-file", str(f)],
    )

    assert result.exit_code == 0
    assert SoulRegistry(registry_dir).get("note_taker").instruction == "From a file."  # type: ignore[union-attr]


def test_create_rejects_both_instruction_sources(registry_dir: Path, tmp_path: Path) -> None:
    f = tmp_path / "instr.md"
    f.write_text("x")
    result = runner.invoke(
        cli_app,
        [
            "soul",
            "create",
            "N",
            "--description",
            "d",
            "--instruction",
            "y",
            "--instruction-file",
            str(f),
        ],
    )

    assert result.exit_code == 2
    assert "not both" in result.output


def test_create_no_input_missing_field_exits_2(registry_dir: Path) -> None:
    result = runner.invoke(cli_app, ["soul", "create", "N", "--instruction", "y", "--no-input"])

    assert result.exit_code == 2
    assert "description" in result.output


def test_create_prompts_for_missing_field(registry_dir: Path) -> None:
    # description omitted → prompted; supply it on stdin.
    result = runner.invoke(
        cli_app, ["soul", "create", "Note Taker", "--instruction", "y"], input="Prompted desc\n"
    )

    assert result.exit_code == 0
    assert SoulRegistry(registry_dir).get("note_taker").description == "Prompted desc"  # type: ignore[union-attr]


def test_create_refuses_overwrite_without_force(registry_dir: Path) -> None:
    SoulRegistry(registry_dir).save(_spec())
    args = ["soul", "create", "Note Taker", "--description", "d", "--instruction", "y"]

    refused = runner.invoke(cli_app, args)
    assert refused.exit_code == 1
    assert "already exists" in refused.output

    forced = runner.invoke(cli_app, [*args, "--force"])
    assert forced.exit_code == 0


# --- show / list -------------------------------------------------------------------


def test_show_unknown_key_exits_1(registry_dir: Path) -> None:
    result = runner.invoke(cli_app, ["soul", "show", "ghost"])

    assert result.exit_code == 1
    assert "no soul" in result.output


def test_show_json(registry_dir: Path) -> None:
    SoulRegistry(registry_dir).save(_spec())

    result = runner.invoke(cli_app, ["--json", "soul", "show", "note_taker"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["name"] == "Note Taker" and data["instruction"] == "You take notes."


def test_list_json(registry_dir: Path) -> None:
    SoulRegistry(registry_dir).save(_spec())

    result = runner.invoke(cli_app, ["--json", "soul", "list"])

    assert result.exit_code == 0
    souls = json.loads(result.output)["souls"]
    assert [s["key"] for s in souls] == ["note_taker"]


# --- delete ------------------------------------------------------------------------


def test_delete_unknown_exits_1(registry_dir: Path) -> None:
    result = runner.invoke(cli_app, ["soul", "delete", "ghost"])

    assert result.exit_code == 1


def test_delete_force_removes(registry_dir: Path) -> None:
    SoulRegistry(registry_dir).save(_spec())

    result = runner.invoke(cli_app, ["soul", "delete", "note_taker", "--force"])

    assert result.exit_code == 0
    assert not (registry_dir / "note_taker.md").exists()


def test_delete_aborts_on_no(registry_dir: Path) -> None:
    SoulRegistry(registry_dir).save(_spec())

    result = runner.invoke(cli_app, ["soul", "delete", "note_taker"], input="n\n")

    assert result.exit_code == 0
    assert (registry_dir / "note_taker.md").exists()  # not deleted


# --- edit --------------------------------------------------------------------------


def test_edit_saves_mutated_markdown(registry_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    SoulRegistry(registry_dir).save(_spec())
    edited = _spec(description="Edited desc").to_markdown()
    monkeypatch.setattr(soul.click, "edit", lambda *a, **k: edited)

    result = runner.invoke(cli_app, ["soul", "edit", "note_taker"])

    assert result.exit_code == 0
    assert SoulRegistry(registry_dir).get("note_taker").description == "Edited desc"  # type: ignore[union-attr]


def test_edit_rename_deletes_old_key(registry_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    SoulRegistry(registry_dir).save(_spec())
    renamed = _spec(name="Scribe").to_markdown()
    monkeypatch.setattr(soul.click, "edit", lambda *a, **k: renamed)

    result = runner.invoke(cli_app, ["soul", "edit", "note_taker"])

    assert result.exit_code == 0
    assert not (registry_dir / "note_taker.md").exists()
    assert (registry_dir / "scribe.md").exists()


def test_edit_invalid_exits_1(registry_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    SoulRegistry(registry_dir).save(_spec())
    monkeypatch.setattr(soul.click, "edit", lambda *a, **k: "no frontmatter here")

    result = runner.invoke(cli_app, ["soul", "edit", "note_taker"])

    assert result.exit_code == 1
    assert "not saved" in result.output


def test_edit_no_change_is_noop(registry_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    SoulRegistry(registry_dir).save(_spec())
    monkeypatch.setattr(soul.click, "edit", lambda *a, **k: None)

    result = runner.invoke(cli_app, ["soul", "edit", "note_taker"])

    assert result.exit_code == 0
    assert "no changes" in result.output


# --- create --ai (smith faked, no key) ---------------------------------------------


def test_ai_forge_saves_under_name(registry_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    forged = SoulDecision(
        action="forge", reason="new capability", spec=_spec(name="Smith Chose This")
    )
    monkeypatch.setattr(soul, "_forge", lambda *a, **k: forged)

    result = runner.invoke(
        cli_app, ["soul", "create", "Mailer", "--ai", "summarize email", "--yes"]
    )

    assert result.exit_code == 0
    saved = SoulRegistry(registry_dir).get("mailer")  # NAME overrides the smith's name
    assert saved is not None and saved.name == "Mailer"


def test_ai_reuse_confirmed_saves_nothing(
    registry_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    SoulRegistry(registry_dir).save(_spec())
    decision = SoulDecision(action="reuse", reason="already fits", soul_key="note_taker")
    monkeypatch.setattr(soul, "_forge", lambda *a, **k: decision)

    # confirm reuse (input 'y'); --yes not passed so it prompts
    result = runner.invoke(cli_app, ["soul", "create", "Mailer", "--ai", "take notes"], input="y\n")

    assert result.exit_code == 0
    assert SoulRegistry(registry_dir).get("mailer") is None  # nothing new saved


def test_ai_reuse_declined_forges_under_name(
    registry_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    SoulRegistry(registry_dir).save(_spec())
    decisions = iter(
        [
            SoulDecision(action="reuse", reason="fits", soul_key="note_taker"),
            SoulDecision(action="forge", reason="forced", spec=_spec(name="Forged")),
        ]
    )
    monkeypatch.setattr(soul, "_forge", lambda *a, **k: next(decisions))

    # decline reuse ('n'), then confirm save ('y')
    result = runner.invoke(cli_app, ["soul", "create", "Mailer", "--ai", "x"], input="n\ny\n")

    assert result.exit_code == 0
    assert SoulRegistry(registry_dir).get("mailer") is not None
