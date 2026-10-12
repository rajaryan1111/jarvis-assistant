"""Start the JARVIS HUD server.

Kept for backwards compatibility with ``python server.py``. The server now lives in
``ultron.api.app``: it binds to 127.0.0.1, runs with debug off, and requires
authentication for every sensitive or state-changing endpoint. See README
("Running the HUD") for first-time setup (``python -m ultron token init``).
"""

import sys

from ultron.api.app import run

if __name__ == "__main__":
    sys.exit(run())
