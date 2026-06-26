"""capabilities tool: surfaces the live exec allowlist + workspace/serve rules to the model."""

from __future__ import annotations

from types import SimpleNamespace

from gaia.tools.capabilities import make_capabilities


def test_lists_allowlist_workspace_and_rules() -> None:
    cap = make_capabilities("allowlist", ("ls", "git", "python"))
    out = cap(tool_context=SimpleNamespace(agent_name="gaia"))

    assert out["status"] == "success"
    assert out["exec"]["allowed_commands"] == ["git", "ls", "python"]  # sorted, the live allowlist
    assert out["exec"]["one_command_only"] is True
    assert "&&" in out["exec"]["no_chaining"]
    assert out["workspace"]["root"].endswith("agents/gaia/workspace")  # the agent's sandbox root
    assert "serve" in out


def test_off_mode_omits_allowlist() -> None:
    out = make_capabilities("off", ("ls",))(tool_context=SimpleNamespace(agent_name="frontend"))

    assert out["exec"]["one_command_only"] is False
    assert "allowed_commands" not in out["exec"]  # no allowlist gate when security is off
    assert out["workspace"]["root"].endswith("agents/frontend/workspace")
