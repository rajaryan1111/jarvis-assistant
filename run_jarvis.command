#!/bin/bash
# Double-click launcher for macOS.
#
# Default: starts the microphone loop, which is the main way to use the assistant:
# say "Ultron", then a command. NO BROWSER IS OPENED. The HUD/API server starts in
# the background only so that approval buttons and the CLI keep working.
#
# Voice needs the optional extra (`uv sync --extra voice`) and a microphone permission;
# without it the launcher says so clearly instead of pretending to listen.
#
# The HUD is OPTIONAL and is never opened automatically. Open it on request with:
#   .venv/bin/python -m ultron open-hud
#   ULTRON_HUD=1 ./run_jarvis.command   (also opens it, for a one-off check)
# The HUD is where high-impact actions are approved with the CONFIRM button.
#
# First-time setup (see README): `uv sync` and `.venv/bin/python -m ultron token init`.
#
# Native desktop controls (menu bar, start/stop, quit): use ./macos/Ultron.app, built
# by macos/build_app.sh. This script is the plain voice loop, for a terminal window.

# Project folder = the folder containing this script (override with JARVIS_PROJECT_DIR).
# ULTRON_PROJECT_DIR is the current name; JARVIS_PROJECT_DIR still works.
PROJECT_DIR="${ULTRON_PROJECT_DIR:-${JARVIS_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}}"
cd "$PROJECT_DIR" || exit 1

# Voice is the default. Set ULTRON_VOICE=0 for a server-only start (no microphone).
# The old JARVIS_VOICE=1 still works and is now simply the default.
JARVIS_VOICE="${ULTRON_VOICE:-${JARVIS_VOICE:-1}}"

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
        echo "The assistant server exited during startup; see the messages above."
        exit 1
    fi
    sleep 0.5
done

# The HUD is NOT opened here. Normal desktop startup must not launch a browser, and
# neither must wake-word activation. It stays one explicit command away:
#   "$PYTHON" -m ultron open-hud
if [ "$ULTRON_HUD" = "1" ]; then
    echo "ULTRON_HUD=1 is set: opening the HUD once (never opened by default)."
    "$PYTHON" -m ultron open-hud || echo "Open http://127.0.0.1:${PORT}/login and paste your API token."
else
    echo "No browser opened. The HUD is available on request:"
    echo "  $PYTHON -m ultron open-hud   (then http://127.0.0.1:${PORT}/login)"
fi

# Optional microphone loop. It runs in the foreground, so closing the window (or
# saying "goodbye") stops it and the trap above stops the server with it.
if [ "$JARVIS_VOICE" = "1" ]; then
    echo "Starting the microphone loop. Say 'goodbye' to stop."
    "$PYTHON" main.py
    echo "Voice loop finished."
else
    echo "Server only, no microphone. Start voice too with: JARVIS_VOICE=1 $0"
    wait "$SERVER_PID"
fi
