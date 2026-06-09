"""Entry point: launch godpy.

Run with ``uv run python main.py``:

* ``python main.py``                    -> local CLI/TUI chat (default).
* ``python main.py whatsapp``           -> WhatsApp backend (QR on first run, see app.run).
* ``python main.py dev``                -> ADK web UI on God (see tool calls + LLM requests).
* ``python main.py --env-file ./.env``  -> read secrets from a specific .env file.

Secrets are read from ``~/.godpy/.env`` by default; ``--env-file`` overrides that
(e.g. point at a repo-local ``.env`` during development). The CLI needs
``GEMINI_API_KEY`` to get real answers from God.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from godpy.app import run, run_cli, run_dev


def main() -> None:
    parser = argparse.ArgumentParser(prog="godpy", description="Launch godpy.")
    parser.add_argument(
        "mode",
        nargs="?",
        default="cli",
        choices=("cli", "whatsapp", "dev"),
        help="cli (default) terminal TUI, whatsapp backend, or dev for the ADK web UI.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Path to a .env file with secrets (default: ~/.godpy/.env).",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="dev mode: web UI host (default: 127.0.0.1)."
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="dev mode: web UI port (default: 8000)."
    )
    args = parser.parse_args()

    if args.mode == "whatsapp":
        run(env_file=args.env_file)
    elif args.mode == "dev":
        run_dev(env_file=args.env_file, host=args.host, port=args.port)
    else:
        run_cli(env_file=args.env_file)


if __name__ == "__main__":
    main()
