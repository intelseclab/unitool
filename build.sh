#!/usr/bin/env bash
# Build UniTool for the current platform (macOS or Linux).
# Usage:  ./build.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Detect platform ────────────────────────────────────────────────────────────
case "$(uname -s)" in
    Darwin*)  PLATFORM="macos"   ;;
    Linux*)   PLATFORM="linux"   ;;
    *)        echo "Use build.bat on Windows."; exit 1 ;;
esac

echo "============================================"
echo " UniTool — building for $PLATFORM"
echo "============================================"

# ── Python ────────────────────────────────────────────────────────────────────
PYTHON="${PYTHON:-python3}"
"$PYTHON" --version

# ── Virtual environment ───────────────────────────────────────────────────────
VENV=".venv-$PLATFORM"
if [ ! -d "$VENV" ]; then
    echo "[1/4] Creating virtual environment: $VENV"
    "$PYTHON" -m venv "$VENV"
fi
# shellcheck disable=SC1090
source "$VENV/bin/activate"

# ── Dependencies ──────────────────────────────────────────────────────────────
echo "[2/4] Installing dependencies..."
pip install -q --upgrade pip
pip install -q pyinstaller
pip install -q -r requirements.txt

# ── icon.png ──────────────────────────────────────────────────────────────────
if [ ! -f "icon.png" ]; then
    echo "[3/4] Generating icon.png from icon.ico..."
    python - <<'PYEOF'
from PIL import Image
Image.open('icon.ico').save('icon.png', 'PNG')
print("icon.png created")
PYEOF
else
    echo "[3/4] icon.png already exists — skipping"
fi

# ── PyInstaller ───────────────────────────────────────────────────────────────
echo "[4/4] Running PyInstaller..."
pyinstaller UniTool.spec --noconfirm

# Rename binary with platform suffix
SRC="dist/UniTool"
DST="dist/UniTool-$PLATFORM"
[ -f "$SRC" ] && mv "$SRC" "$DST"

echo ""
echo "============================================"
echo " Build SUCCESS"
echo " Output: $DST"
echo "============================================"
