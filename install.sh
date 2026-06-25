#!/usr/bin/env bash
# install.sh — Manual install script for Mural
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE
#
# Usage:
#   ./install.sh          # install to ~/.local (user-only, no sudo needed)
#   ./install.sh --system # install to /usr (requires sudo)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${HOME}/.local"
SYSTEM_INSTALL=0

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
for arg in "$@"; do
  case "$arg" in
    --system) SYSTEM_INSTALL=1; PREFIX="/usr" ;;
    --prefix=*) PREFIX="${arg#--prefix=}" ;;
    --help|-h)
      echo "Usage: $0 [--system] [--prefix=PATH]"
      echo "  --system        Install to /usr (requires sudo)"
      echo "  --prefix=PATH   Install to PATH (default: ~/.local)"
      exit 0 ;;
  esac
done

BIN_DIR="${PREFIX}/bin"
LIB_DIR="${PREFIX}/lib/python3/dist-packages"
SHARE_DIR="${PREFIX}/share/mural"
PLASMA_PLUGIN_DIR="${HOME}/.local/share/plasma/wallpapers/com.mural.wallpaper"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

echo "Installing Mural to ${PREFIX} ..."

# ---------------------------------------------------------------------------
# Python dependencies
# ---------------------------------------------------------------------------
echo "[1/6] Installing Python dependencies..."
pip install --quiet -r "${SCRIPT_DIR}/requirements.txt"

# ---------------------------------------------------------------------------
# Python package
# ---------------------------------------------------------------------------
echo "[2/6] Installing mural Python package..."
pip install --quiet -e "${SCRIPT_DIR}"

# ---------------------------------------------------------------------------
# Entry point scripts
# ---------------------------------------------------------------------------
echo "[3/6] Installing entry point scripts..."
mkdir -p "${BIN_DIR}"

cat > "${BIN_DIR}/mural" << 'EOF'
#!/usr/bin/env python3
from mural.main import main
main()
EOF
chmod +x "${BIN_DIR}/mural"

cat > "${BIN_DIR}/mural-core" << 'EOF'
#!/usr/bin/env python3
from mural.core.service import main
main()
EOF
chmod +x "${BIN_DIR}/mural-core"

# ---------------------------------------------------------------------------
# Plasma wallpaper plugin
# ---------------------------------------------------------------------------
echo "[4/6] Installing Plasma wallpaper plugin..."
mkdir -p "${PLASMA_PLUGIN_DIR}"
cp -r "${SCRIPT_DIR}/plasma-plugin/." "${PLASMA_PLUGIN_DIR}/"
echo "     Installed to ${PLASMA_PLUGIN_DIR}"

# ---------------------------------------------------------------------------
# systemd user service
# ---------------------------------------------------------------------------
echo "[5/6] Installing systemd user service..."
mkdir -p "${SYSTEMD_USER_DIR}"
cp "${SCRIPT_DIR}/systemd/mural-core.service" "${SYSTEMD_USER_DIR}/"
systemctl --user daemon-reload
systemctl --user enable --now mural-core.service && \
  echo "     mural-core.service enabled and started" || \
  echo "     Warning: could not enable service — start manually with: systemctl --user start mural-core.service"

# ---------------------------------------------------------------------------
# Config directory
# ---------------------------------------------------------------------------
echo "[6/6] Creating config directory..."
mkdir -p "${HOME}/.config/mural"
mkdir -p "${HOME}/.local/share/mural/downloads"
mkdir -p "${HOME}/.cache/mural"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "Mural installed successfully."
echo ""
echo "Launch the GUI:          mural"
echo "Service status:          systemctl --user status mural-core.service"
echo "Service logs:            journalctl --user -u mural-core.service -f"
echo ""
echo "To uninstall:            systemctl --user disable --now mural-core.service"
echo "                         rm -rf ${PLASMA_PLUGIN_DIR}"
echo "                         rm ${BIN_DIR}/mural ${BIN_DIR}/mural-core"
