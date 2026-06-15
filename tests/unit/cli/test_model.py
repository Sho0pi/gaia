"""``gaia model`` wizard: provider auth, live model fallback, and default selection."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml
from typer.testing import CliRunner

from gaia.cli import app
from gaia.cli import model as model_mod
from gaia.cli._envfile import get_env_var
from gaia.config import Settings

runner = CliRunner()


def _settings(tmp_path: Path) -> Settings:
    return Settings(config_path=tmp_path / "gaia.yaml", _env_file=None)  # type: ignore[call-arg]


def _config(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load((path / "gaia.yaml").read_text()))


def _clear_model_env(monkeypatch: Any) -> None:
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def test_gemini_api_key_sets_default_model(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _clear_model_env(monkeypatch)
    settings = _settings(tmp_path)
    monkeypatch.setattr("gaia.config.get_settings", lambda env_file=None: settings)
    monkeypatch.setattr("gaia.constants.ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(model_mod, "_fetch_models", lambda provider, key: ["gemini-live"])

    # key, default provider #1, default model #1
    result = runner.invoke(app, ["model", "gemini"], input="secret\n1\n1\n")

    assert result.exit_code == 0, result.output
    assert get_env_var(tmp_path / ".env", "GEMINI_API_KEY") == "secret"
    cfg = _config(tmp_path)
    assert cfg["llm"]["provider"] == "gemini"
    assert cfg["llm"]["model"] == "gemini-live"


def test_openai_oauth_delegates_and_sets_use_oauth(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _clear_model_env(monkeypatch)
    settings = _settings(tmp_path)
    monkeypatch.setattr("gaia.config.get_settings", lambda env_file=None: settings)
    monkeypatch.setattr(model_mod, "_openai_oauth_configured", lambda: False)
    called: list[str] = []
    monkeypatch.setattr(
        "gaia.app.run_auth", lambda provider, *, env_file=None: called.append(provider)
    )

    # method #2 oauth, default provider #1, fallback model #1
    result = runner.invoke(app, ["model", "openai", "--no-fetch"], input="2\n1\n1\n")

    assert result.exit_code == 0, result.output
    assert called == ["openai"]
    cfg = _config(tmp_path)
    assert cfg["llm"]["provider"] == "openai"
    assert cfg["llm"]["openai"]["use_oauth"] is True


def test_openai_oauth_configured_can_be_kept(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _clear_model_env(monkeypatch)
    settings = _settings(tmp_path)
    monkeypatch.setattr("gaia.config.get_settings", lambda env_file=None: settings)
    monkeypatch.setattr(model_mod, "_openai_oauth_configured", lambda: True)
    called: list[str] = []
    monkeypatch.setattr(
        "gaia.app.run_auth", lambda provider, *, env_file=None: called.append(provider)
    )

    # method #2 oauth, do not re-login, default provider #1, fallback model #1
    result = runner.invoke(app, ["model", "openai", "--no-fetch"], input="2\nn\n1\n1\n")

    assert result.exit_code == 0, result.output
    assert called == []
    assert "kept existing OpenAI OAuth" in result.output
    cfg = _config(tmp_path)
    assert cfg["llm"]["openai"]["use_oauth"] is True


def test_anthropic_fetch_failure_uses_fallback(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _clear_model_env(monkeypatch)
    settings = _settings(tmp_path)
    monkeypatch.setattr("gaia.config.get_settings", lambda env_file=None: settings)
    monkeypatch.setattr("gaia.constants.ENV_FILE", tmp_path / ".env")

    def boom(provider: str, key: str) -> list[str]:
        raise RuntimeError("offline")

    monkeypatch.setattr(model_mod, "_fetch_models", boom)

    result = runner.invoke(app, ["model", "anthropic"], input="sk-ant\n1\n1\n")

    assert result.exit_code == 0, result.output
    assert "using fallback" in result.output
    assert get_env_var(tmp_path / ".env", "ANTHROPIC_API_KEY") == "sk-ant"
    cfg = _config(tmp_path)
    assert cfg["llm"]["provider"] == "anthropic"
    assert cfg["llm"]["model"] == "claude-3-5-sonnet-latest"


def test_bare_model_selects_multiple_providers(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _clear_model_env(monkeypatch)
    settings = _settings(tmp_path)
    monkeypatch.setattr("gaia.config.get_settings", lambda env_file=None: settings)
    monkeypatch.setattr("gaia.constants.ENV_FILE", tmp_path / ".env")

    # providers 1,3; gemini key; anthropic key; default provider #2 (anthropic); model #1
    result = runner.invoke(app, ["model", "--no-fetch"], input="1,3\ng-key\na-key\n2\n1\n")

    assert result.exit_code == 0, result.output
    assert get_env_var(tmp_path / ".env", "GEMINI_API_KEY") == "g-key"
    assert get_env_var(tmp_path / ".env", "ANTHROPIC_API_KEY") == "a-key"
    assert _config(tmp_path)["llm"]["provider"] == "anthropic"


def test_unknown_provider_exits_2(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _clear_model_env(monkeypatch)
    monkeypatch.setattr("gaia.config.get_settings", lambda env_file=None: _settings(tmp_path))

    result = runner.invoke(app, ["model", "ollama"])

    assert result.exit_code == 2
    assert "unknown provider" in result.output


def test_fetch_parsers() -> None:
    gemini = {
        "models": [{"name": "models/gemini-x", "supportedGenerationMethods": ["generateContent"]}]
    }
    openai = {"data": [{"id": "gpt-x"}, {"id": "tts-1"}]}
    anthropic = {"data": [{"id": "claude-x"}]}

    assert model_mod._parse_gemini_models(cast(dict[str, object], gemini)) == ["gemini-x"]
    assert model_mod._parse_openai_models(cast(dict[str, object], openai)) == ["gpt-x"]
    assert model_mod._parse_anthropic_models(cast(dict[str, object], anthropic)) == ["claude-x"]
