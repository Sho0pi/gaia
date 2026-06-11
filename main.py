"""DEPRECATED entry point — use the ``godpy`` command instead (``uv run godpy --help``).

Maps the old argparse modes onto the new Typer CLI and delegates:
``cli`` → ``chat``, ``whatsapp`` → ``serve``, ``dev`` → ``dev`` (with --host/--port),
``auth <provider>`` → ``llm auth <provider>``; ``--env-file`` is forwarded.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_DEPRECATION = (
    "main.py is deprecated; use the `godpy` command instead (uv run godpy --help). "
    "This shim will be removed in a future release."
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="godpy", description="Launch godpy (deprecated shim).")
    parser.add_argument(
        "mode", nargs="?", default="cli", choices=("cli", "whatsapp", "dev", "auth")
    )
    parser.add_argument("provider", nargs="?", default=None)
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser


def translate(argv: list[str]) -> list[str]:
    """Map old ``main.py`` argv onto the new ``godpy`` CLI argv."""
    args = _build_parser().parse_args(argv)
    new: list[str] = []
    if args.env_file is not None:  # global flag: must precede the subcommand
        new += ["--env-file", str(args.env_file)]
    if args.mode == "whatsapp":
        new.append("serve")
    elif args.mode == "dev":
        new += ["dev", "--host", args.host, "--port", str(args.port)]
    elif args.mode == "auth":
        new += ["llm", "auth", args.provider or "openai"]
    else:
        new.append("chat")
    return new


def main() -> None:
    print(_DEPRECATION, file=sys.stderr)
    from godpy.cli import main as cli_main

    cli_main(translate(sys.argv[1:]))


if __name__ == "__main__":
    main()
