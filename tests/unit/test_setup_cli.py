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


def _llm(config_path: Path) -> dict:  # type: ignore[type-arg]
    import yaml

    return (yaml.safe_load(config_path.read_text()) or {}).get("llm", {})


def test_model_gemini_flag_path(tmp_path: Path) -> None:
    from gaia import constants

    result = runner.invoke(
        app,
        [
            "setup",
            "model",
            "--provider",
            "gemini",
            "--api-key",
            "gk",
            "--model",
            "gemini-2.5-flash",
        ],
    )
    assert result.exit_code == 0, result.output
    llm = _llm(constants.CONFIG_PATH)
    assert llm["provider"] == "gemini" and llm["model"] == "gemini-2.5-flash"
    assert get_env_var(constants.ENV_FILE, "GEMINI_API_KEY") == "gk"


def test_model_openai_oauth_flag(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import gaia.app
    from gaia import constants

    monkeypatch.setattr(gaia.app, "run_auth", lambda *a, **k: None)  # skip the device flow
    result = runner.invoke(
        app, ["setup", "model", "--provider", "openai", "--oauth", "--model", "gpt-5.5"]
    )
    assert result.exit_code == 0, result.output
    llm = _llm(constants.CONFIG_PATH)
    assert (
        llm["openai"]["use_oauth"] is True
        and llm["provider"] == "openai"
        and llm["model"] == "gpt-5.5"
    )


def test_model_openai_key_flag(tmp_path: Path) -> None:
    # OpenAI via API key (same provider, different auth) — no oauth.
    from gaia import constants

    result = runner.invoke(
        app, ["setup", "model", "--provider", "openai", "--api-key", "ok", "--model", "gpt-4o"]
    )
    assert result.exit_code == 0, result.output
    llm = _llm(constants.CONFIG_PATH)
    assert llm["provider"] == "openai" and llm["openai"]["use_oauth"] is False
    assert get_env_var(constants.ENV_FILE, "OPENAI_API_KEY") == "ok"


def test_model_oauth_kept_when_session_exists(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Already-configured oauth session + decline override -> run_auth NOT called, oauth kept.
    import typer

    import gaia.app
    from gaia.providers.openai import store

    monkeypatch.setattr(store, "credentials_path", lambda: _ExistingPath())  # session "exists"
    called = {"auth": False}

    def fake_auth(*a, **k):  # type: ignore[no-untyped-def]
        called["auth"] = True

    monkeypatch.setattr(gaia.app, "run_auth", fake_auth)
    monkeypatch.setattr(typer, "confirm", lambda *a, **k: False)  # decline re-login
    result = runner.invoke(
        app, ["setup", "model", "--provider", "openai", "--oauth", "--model", "gpt-5.5"]
    )
    assert result.exit_code == 0, result.output
    assert called["auth"] is False  # kept existing session, didn't re-run the device flow


class _ExistingPath:
    def exists(self) -> bool:
        return True


def test_admin_flag_sets_config(tmp_path: Path) -> None:
    import yaml

    from gaia import constants

    result = runner.invoke(app, ["setup", "admin", "--id", "telegram:12345"])
    assert result.exit_code == 0, result.output
    data = yaml.safe_load(constants.CONFIG_PATH.read_text()) or {}
    assert data["admin"] == ["telegram:12345"]


def test_browser_flag_sets_backend(tmp_path: Path) -> None:
    import yaml

    from gaia import constants

    result = runner.invoke(app, ["setup", "browser", "--backend", "native", "--no-headless"])
    assert result.exit_code == 0, result.output
    data = yaml.safe_load(constants.CONFIG_PATH.read_text()) or {}
    assert data["browser"]["backend"] == "native" and data["browser"]["headless"] is False


def test_mcp_flag_appends_server(tmp_path: Path) -> None:
    import yaml

    from gaia import constants

    result = runner.invoke(
        app,
        ["setup", "mcp", "--name", "gh", "--transport", "stdio", "--command", "bunx", "--arg", "x"],
    )
    assert result.exit_code == 0, result.output
    data = yaml.safe_load(constants.CONFIG_PATH.read_text()) or {}
    servers = data["mcp"]["servers"]
    assert (
        servers[-1]["name"] == "gh"
        and servers[-1]["command"] == "bunx"
        and servers[-1]["args"] == ["x"]
    )


def test_walkthrough_runs_each_step(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import typer

    from gaia.cli import setup

    called: list[str] = []

    def rec(name: str):  # type: ignore[no-untyped-def]
        def step(_ctx: object) -> None:
            called.append(name)

        return step

    for n in ("model", "connectors", "admin", "search", "browser"):
        monkeypatch.setattr(setup, n, rec(n))
    monkeypatch.setattr(typer, "confirm", lambda *a, **k: False)  # decline the optional MCP step

    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 0, result.output
    assert called == ["model", "connectors", "admin", "search", "browser"]
    assert "setup complete" in result.output


def test_search_honors_env_file_flag(tmp_path: Path) -> None:
    # --env-file must route the secret write there, not to the default ~/.gaia/.env.
    from gaia import constants

    alt = tmp_path / "alt.env"
    result = runner.invoke(
        app, ["--env-file", str(alt), "setup", "search", "--engine", "brave", "--api-key", "k"]
    )
    assert result.exit_code == 0, result.output
    assert get_env_var(alt, "BRAVE_API_KEY") == "k"
    assert get_env_var(constants.ENV_FILE, "BRAVE_API_KEY") is None


def test_model_multi_select_configures_many_and_picks_active(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Configure Gemini + ChatGPT in one run; active = gemini.
    import typer

    import gaia.app
    from gaia import constants

    # provider multi-select → both; OpenAI auth method → ChatGPT; active → gemini; model → flash.
    monkeypatch.setattr("gaia.cli._select.select_many", lambda *a, **k: ["openai", "gemini"])

    def fake_select_one(title, options, default=None):  # type: ignore[no-untyped-def]
        if title.startswith("OpenAI"):
            return "chatgpt"
        if title == "Active provider":
            return "gemini"
        return "gemini-2.5-flash"

    monkeypatch.setattr("gaia.cli._select.select_one", fake_select_one)
    monkeypatch.setattr(gaia.app, "run_auth", lambda *a, **k: None)  # ChatGPT device flow
    monkeypatch.setattr(typer, "prompt", lambda *a, **k: "gk")  # Gemini key prompt
    monkeypatch.setattr(typer, "confirm", lambda *a, **k: True)  # replace any existing key
    monkeypatch.setattr("gaia.cli._models.available_models", lambda *a, **k: [])

    result = runner.invoke(app, ["setup", "model"])
    assert result.exit_code == 0, result.output
    llm = _llm(constants.CONFIG_PATH)
    assert llm["provider"] == "gemini" and llm["openai"]["use_oauth"] is False
    assert get_env_var(constants.ENV_FILE, "GEMINI_API_KEY") == "gk"


def test_walkthrough_skips_step_on_interrupt(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Ctrl-C/Esc in a step skips it and moves on — never aborts the whole wizard.
    import typer

    from gaia.cli import setup

    called: list[str] = []

    def mk(name: str, raise_: BaseException | None = None):  # type: ignore[no-untyped-def]
        def step(_ctx: object) -> None:
            if raise_ is not None:
                raise raise_
            called.append(name)

        return step

    monkeypatch.setattr(setup, "model", mk("model", KeyboardInterrupt()))
    for n in ("connectors", "admin", "search", "browser"):
        monkeypatch.setattr(setup, n, mk(n))
    monkeypatch.setattr(typer, "confirm", lambda *a, **k: False)

    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 0, result.output
    assert called == ["connectors", "admin", "search", "browser"]  # model skipped, rest ran


def test_setup_connectors_subcommand_removed() -> None:
    # Deduped: `gaia connect` is the connector command; `setup connectors` is gone.
    result = runner.invoke(app, ["setup", "connectors"])
    assert result.exit_code != 0
    assert "No such command" in result.output or "no such command" in result.output.lower()
