"""Deprecation shim: old ``main.py`` argv maps onto the new ``godpy`` CLI."""

from __future__ import annotations

from typing import Any

import main
import pytest


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ([], ["chat"]),
        (["cli"], ["chat"]),
        (["whatsapp"], ["serve"]),
        (["dev"], ["dev", "--host", "127.0.0.1", "--port", "8000"]),
        (["dev", "--port", "9001"], ["dev", "--host", "127.0.0.1", "--port", "9001"]),
        (["auth"], ["llm", "auth", "openai"]),
        (["auth", "openai"], ["llm", "auth", "openai"]),
        (["--env-file", "x.env", "cli"], ["--env-file", "x.env", "chat"]),
        (["whatsapp", "--env-file", "x.env"], ["--env-file", "x.env", "serve"]),
    ],
)
def test_translate(old: list[str], new: list[str]) -> None:
    assert main.translate(old) == new


def test_main_warns_and_delegates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    called: dict[str, Any] = {}
    monkeypatch.setattr("godpy.cli.main", lambda argv=None: called.update(argv=argv))
    monkeypatch.setattr("sys.argv", ["main.py", "dev", "--port", "9001"])

    main.main()

    assert called == {"argv": ["dev", "--host", "127.0.0.1", "--port", "9001"]}
    assert "deprecated" in capsys.readouterr().err
