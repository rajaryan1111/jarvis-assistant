#!/bin/bash
# Build the native Ultron menu-bar application.
#
#   ./macos/build_app.sh
#
# Produces macos/Ultron.app - a double-clickable, browser-free desktop entry point.
# The built app runs the SAME voice session as the terminal loop (see
# ultron/desktop/controller.py), so there is one voice pipeline, not two.
#
# Requirements (macOS only):
#   * the `macos` extra:  uv sync --extra macos   (installs PyObjC)
#   * a Python that can import the project: the .venv created by `uv sync`
#
# Signing:
#   By default the app is signed ad-hoc (identity "-"), which is enough to run on the
#   Mac that built it. To distribute, set ULTRON_CODESIGN_IDENTITY to your Developer ID
#   Application certificate before running this script. Never put a signing password in
#   this repository.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
APP="$HERE/Ultron.app"
IDENTITY="${ULTRON_CODESIGN_IDENTITY:--}"   # "-" means ad-hoc signing

cd "$ROOT"

# --- the interpreter the built app will use -------------------------------
if [ -x "$ROOT/.venv/bin/python" ]; then
    PYTHON="$ROOT/.venv/bin/python"
elif [ -x "$ROOT/jarvis_env/bin/python" ]; then
    PYTHON="$ROOT/jarvis_env/bin/python"
else
    echo "No virtual environment found. Run 'uv sync' in $ROOT first." >&2
    exit 1
fi

# --- the optional native dependency ---------------------------------------
if ! "$PYTHON" -c "import AppKit" >/dev/null 2>&1; then
    echo "PyObjC is missing. Install the native extra first:" >&2
    echo "    uv sync --extra macos" >&2
    exit 1
fi

echo "Building $APP ..."
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# --- the launcher ----------------------------------------------------------
# A tiny shell stub is used instead of a symlink so the app keeps working when the
# .venv is rebuilt, and so the interpreter can be found from the bundle's location.
cat > "$APP/Contents/MacOS/UltronMenuBar" <<LAUNCHER
#!/bin/bash
# Resolve the project root: ULTRON_PROJECT_DIR, else the repo that contains this bundle.
ROOT="\${ULTRON_PROJECT_DIR:-\${JARVIS_PROJECT_DIR:-$(cd "$ROOT" && pwd)}}"
cd "\$ROOT" || exit 1
PYTHON="\${ULTRON_PYTHON:-}"
if [ -z "\$PYTHON" ]; then
    if [ -x "\$ROOT/.venv/bin/python" ]; then PYTHON="\$ROOT/.venv/bin/python"
    elif [ -x "\$ROOT/jarvis_env/bin/python" ]; then PYTHON="\$ROOT/jarvis_env/bin/python"
    else
        echo "No virtual environment found. Run 'uv sync' in \$ROOT." >&2
        exit 1
    fi
fi
exec "\$PYTHON" -m macos.menubar "\$@"
LAUNCHER
chmod +x "$APP/Contents/MacOS/UltronMenuBar"

# --- the Python entry point ------------------------------------------------
cp "$HERE/menubar.py" "$APP/Contents/Resources/menubar.py"
cp "$HERE/../ultron/desktop/controller.py" "$APP/Contents/Resources/controller.py" 2>/dev/null || true

# --- the bundle metadata ---------------------------------------------------
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"

# The build date and the source commit make a support answer possible ("which build
# is this?"). Neither is a secret.
COMMIT="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
BUILT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
/usr/bin/plutil -replace UltronBuildCommit -string "$COMMIT" "$APP/Contents/Info.plist"
/usr/bin/plutil -replace UltronBuildDate -string "$BUILT" "$APP/Contents/Info.plist"

# --- code signing ----------------------------------------------------------
if [ "$IDENTITY" = "-" ]; then
    echo "Signing ad-hoc (local use only). Set ULTRON_CODESIGN_IDENTITY to distribute."
fi
codesign --force --sign "$IDENTITY" "$APP" 2>&1 | sed 's/^/  codesign: /' || {
    echo "  codesign failed; the app will still run locally but macOS may warn." >&2
}

echo
echo "Built $APP"
echo "  commit : $COMMIT"
echo "  built  : $BUILT"
echo "  signed : $IDENTITY"
echo
echo "Run it by double-clicking in Finder, or:  open '$APP'"
echo "It starts with NO BROWSER OPENED. The HUD stays one menu click away."
