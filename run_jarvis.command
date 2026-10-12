#!/bin/bash
# Double-click launcher for macOS: starts the JARVIS HUD server and opens the HUD
# with a one-time login link. First-time setup (see README): `uv sync` and
# `.venv/bin/python -m ultron token init`.

# Project folder = the folder containing this script (override with JARVIS_PROJECT_DIR).
PROJECT_DIR="${JARVIS_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
cd "$PROJECT_DIR" || exit 1

if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif [ -x "jarvis_env/bin/python" ]; then
    PYTHON="jarvis_env/bin/python"
else
    echo "No virtual environment found. Run 'uv sync' in $PROJECT_DIR first (see README)."
    exit 1
fi

PORT="${ULTRON_PORT:-5001}"

# Start the server in the background (binds to 127.0.0.1 only).
"$PYTHON" server.py &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null' INT TERM

# Wait up to ~20 s for the server to become healthy.
for _ in $(seq 1 40); do
    if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
        break
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "The JARVIS server exited during startup; see the messages above."
        exit 1
    fi
    sleep 0.5
done

# Open the HUD (set ULTRON_BROWSER_APP="Google Chrome" to pick a browser).
"$PYTHON" -m ultron open-hud || echo "Open http://127.0.0.1:${PORT}/login and paste your API token."

wait "$SERVER_PID"
