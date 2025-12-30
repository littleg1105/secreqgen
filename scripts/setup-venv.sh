#!/bin/bash
# Setup virtual environment for SecReqGen
#
# This script creates a venv with PYTHONPATH configured to bypass
# the macOS Sequoia .pth hidden flag issue (Python security feature).
#
# Usage: ./scripts/setup-venv.sh

set -e

VENV_PATH="${1:-.venv}"
PYTHON_VERSION="${2:-python3.13}"

echo "=== SecReqGen Virtual Environment Setup ==="
echo "Python: $PYTHON_VERSION"
echo "Venv: $VENV_PATH"
echo ""

# Create venv
echo "[1/4] Creating virtual environment..."
$PYTHON_VERSION -m venv "$VENV_PATH"

# Add PYTHONPATH to activate script (bypasses .pth file issues)
echo "[2/4] Configuring PYTHONPATH in activate script..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cat >> "$VENV_PATH/bin/activate" << EOF

# SecReqGen: Add src to PYTHONPATH to bypass macOS .pth hidden flag issue
# See: https://github.com/python/cpython/issues/113659
export PYTHONPATH="$PROJECT_ROOT/src:\$PYTHONPATH"
EOF

# Install dependencies
echo "[3/4] Installing dependencies with Poetry..."
"$VENV_PATH/bin/pip" install -q poetry
"$VENV_PATH/bin/poetry" install

# Verify
echo "[4/4] Verifying installation..."
"$VENV_PATH/bin/python" -c "from secreqgen import __version__; print(f'secreqgen v{__version__} - OK')"

echo ""
echo "=== Setup Complete ==="
echo "Activate with: source $VENV_PATH/bin/activate"
