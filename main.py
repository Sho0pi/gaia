"""Entry point: launch godpy.

Run with ``uv run python main.py``:

* ``python main.py``            -> local CLI/TUI chat (default).
* ``python main.py whatsapp``   -> WhatsApp backend (QR on first run, see app.run).

The CLI needs ``GEMINI_API_KEY`` in ``.env`` to get real answers from God.
"""

import sys

from godpy.app import run, run_cli

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "whatsapp":
        run()
    else:
        run_cli()
