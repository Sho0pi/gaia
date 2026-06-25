"""`gaia setup search` — scriptable (flag) path writes the engine config + the Brave key."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from gaia.cli import app
from gaia.cli._envfile import get_env_var

runner = CliRunner()


def _engine(config_path: Path) -> str | None:
    import yaml

    data = yaml.safe_load(config_path.read_text()) or {}
    return (data.get("tools") or {}).get("web_search", {}).get("engine")


def test_search_duckduckgo_sets_engine_no_key(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # _isolate_home points the home paths at tmp; CONFIG_PATH / ENV_FILE land there.
    from gaia import constants

    result = runner.invoke(app, ["setup", "search", "--engine", "duckduckgo"])
    assert result.exit_code == 0, result.output
    assert _engine(constants.CONFIG_PATH) == "duckduckgo"
    assert get_env_var(constants.ENV_FILE, "BRAVE_API_KEY") is None  # ddg needs no key


def test_search_brave_saves_key_and_engine(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from gaia import constants

    result = runner.invoke(
        app, ["setup", "search", "--engine", "brave", "--api-key", "brv-secret-123"]
    )
    assert result.exit_code == 0, result.output
    assert _engine(constants.CONFIG_PATH) == "brave"
    assert get_env_var(constants.ENV_FILE, "BRAVE_API_KEY") == "brv-secret-123"


def test_search_unknown_engine_errors(tmp_path: Path) -> None:
    result = runner.invoke(app, ["setup", "search", "--engine", "bing"])
    assert result.exit_code == 1 and "unknown engine" in result.output


def test_select_one_numbered_fallback(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Non-TTY (scripts/tests): select_one falls back to a numbered prompt, returns the value.
    import typer

    from gaia.cli import _select

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(typer, "prompt", lambda *a, **k: "2")  # pick the 2nd option
    out = _select.select_one(
        "Engine",
        [("duckduckgo", "DuckDuckGo", ""), ("brave", "Brave", "key")],
        default="duckduckgo",
    )
    assert out == "brave"


def test_select_one_numbered_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import typer

    from gaia.cli import _select

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    # empty input -> typer returns the default (the start index = duckduckgo's "1")
    monkeypatch.setattr(typer, "prompt", lambda *a, **k: k.get("default", "1"))
    out = _select.select_one(
        "Engine", [("duckduckgo", "DDG", ""), ("brave", "Brave", "")], default="brave"
    )
    assert out == "brave"  # default highlighted -> index 2 -> brave
