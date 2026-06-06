"""Entry point: launch godpy and connect it to WhatsApp.

Run with ``uv run python main.py``. With no WhatsApp Business creds configured the
launcher starts the regular-account backend and prints a QR code to scan on first
run; the paired session is persisted so later runs reconnect automatically.
"""

from godpy.app import run

if __name__ == "__main__":
    run()
