#!/usr/bin/env bash
# Build StreamsClient as a standalone macOS .app bundle.
#
# Usage:
#   ./build.sh           # build the app
#   ./build.sh clean     # remove build artifacts
#
# Prerequisites:
#   - Python 3.10+
#   - VLC installed at /Applications/VLC.app (runtime dependency)
#   - pip install pyinstaller  (build dependency only)
#
# Output:
#   dist/StreamsClient.app   — drag to /Applications to install
set -euo pipefail
cd "$(dirname "$0")"

if [ "${1:-}" = "clean" ]; then
    echo "Cleaning build artifacts..."
    rm -rf build/ dist/ __pycache__/
    echo "Done."
    exit 0
fi

# Activate venv if present.
if [ -d .venv ]; then
    source .venv/bin/activate
fi

# Ensure PyInstaller is available.
if ! python -m PyInstaller --version &>/dev/null; then
    echo "Installing PyInstaller..."
    pip install pyinstaller --quiet
fi

echo "Building StreamsClient.app..."
python -m PyInstaller StreamsClient.spec \
    --noconfirm \
    --clean \
    --distpath dist \
    --workpath build 2>&1 | tail -20

if [ -d "dist/StreamsClient.app" ]; then
    echo ""
    echo "Build successful!"
    echo "  App:  dist/StreamsClient.app"
    echo ""
    echo "To install, drag StreamsClient.app to /Applications."
    echo "Note: VLC must be installed (/Applications/VLC.app)."
else
    echo ""
    echo "Build failed. Check output above for errors."
    exit 1
fi
