#!/bin/bash
# Double-click launcher for macOS.
#
# Default: starts the JARVIS HUD/API server and opens the HUD with a one-time login
# link (the HUD is where high-impact actions are approved with the CONFIRM button).
#
# JARVIS_VOICE=1 also runs the microphone loop, which is the main way to use JARVIS:
#   JARVIS_VOICE=1 ./run_jarvis.command
# or set it once in the script below. Voice needs the optional extra (`uv sync --extra
# voice`) and a microphone permission; without it the launcher says so and still
# starts the HUD, so the assistant stays usable either way.
#
# First-time setup (see README): `uv sync` and `.venv/bin/python -m ultron token init`.

# Project folder = the folder containing this script (override with JARVIS_PROJECT_DIR).
PROJECT_DIR="${JARVIS_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
cd "$PROJECT_DIR" || exit 1

# Set JARVIS_VOICE=1 here to always start the microphone loop as well.
JARVIS_VOICE="${JARVIS_VOICE:-0}"

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
cleanup() { kill "$SERVER_PID" 2>/dev/null; }
trap cleanup INT TERM EXIT

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

# Optional microphone loop. It runs in the foreground, so closing the window (or
# saying "goodbye") stops it and the trap above stops the server with it.
if [ "$JARVIS_VOICE" = "1" ]; then
    echo "Starting the microphone loop. Say 'goodbye' to stop."
    "$PYTHON" main.py
    echo "Voice loop finished."
else
    echo "HUD only. Start the microphone loop too with: JARVIS_VOICE=1 $0"
    wait "$SERVER_PID"
fi
