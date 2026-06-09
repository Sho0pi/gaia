"""Entry point: launch godpy.

Run with ``uv run python main.py``:

* ``python main.py``                    -> local CLI/TUI chat (default).
* ``python main.py whatsapp``           -> WhatsApp backend (QR on first run, see app.run).
* ``python main.py auth openai-chatgpt``-> sign in with ChatGPT (device-code OAuth).
* ``python main.py --env-file ./.env``  -> read secrets from a specific .env file.

Secrets are read from ``~/.godpy/.env`` by default; ``--env-file`` overrides that
(e.g. point at a repo-local ``.env`` during development). The CLI needs
``GEMINI_API_KEY`` to get real answers from God.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from godpy.app import run, run_auth, run_cli


def main() -> None:
    parser = argparse.ArgumentParser(prog="godpy", description="Launch godpy.")
    parser.add_argument(
        "mode",
        nargs="?",
        default="cli",
        choices=("cli", "whatsapp", "auth"),
        help="cli (default) terminal TUI, whatsapp backend, or auth to sign in to a provider.",
    )
    parser.add_argument(
        "provider",
        nargs="?",
        default=None,
        help="auth mode: the provider to sign in to (e.g. openai-chatgpt).",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Path to a .env file with secrets (default: ~/.godpy/.env).",
    )
    args = parser.parse_args()

    if args.mode == "whatsapp":
        run(env_file=args.env_file)
    elif args.mode == "auth":
        run_auth(args.provider or "openai-chatgpt", env_file=args.env_file)
    else:
        run_cli(env_file=args.env_file)


if __name__ == "__main__":
    main()
