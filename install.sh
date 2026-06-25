#!/usr/bin/env bash
# install.sh — Manual install script for Mural
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE
#
# Usage:
#   ./install.sh
#
# Installs to ~/.local/bin with a self-contained venv at
# ~/.local/share/mural/venv — no sudo required.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
for arg in "$@"; do
  case "$arg" in
    --help|-h)
      echo "Usage: $0"
      echo "  Installs Mural to ~/.local/bin with a venv at ~/.local/share/mural/venv"
      exit 0 ;;
  esac
done

BIN_DIR="${HOME}/.local/bin"
VENV_DIR="${HOME}/.local/share/mural/venv"
PLASMA_PLUGIN_DIR="${HOME}/.local/share/plasma/wallpapers/com.mural.wallpaper"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

echo "Installing Mural ..."

# ---------------------------------------------------------------------------
# Virtual environment
# ---------------------------------------------------------------------------
echo "[1/6] Creating virtual environment at ${VENV_DIR} ..."
mkdir -p "${HOME}/.local/share/mural"
python3 -m venv "${VENV_DIR}"
VENV_PIP="${VENV_DIR}/bin/pip"
VENV_PYTHON="${VENV_DIR}/bin/python"

# ---------------------------------------------------------------------------
# Python dependencies
# ---------------------------------------------------------------------------
echo "[2/6] Installing Python dependencies into venv..."
"${VENV_PIP}" install --quiet --upgrade pip
"${VENV_PIP}" install --quiet -r "${SCRIPT_DIR}/requirements.txt"
"${VENV_PIP}" install --quiet -e "${SCRIPT_DIR}"

# ---------------------------------------------------------------------------
# Entry point scripts
# ---------------------------------------------------------------------------
echo "[3/6] Installing entry point scripts to ${BIN_DIR} ..."
mkdir -p "${BIN_DIR}"

cat > "${BIN_DIR}/mural" << EOF
#!/usr/bin/env bash
exec "${VENV_PYTHON}" -m mural.main "\$@"
EOF
chmod +x "${BIN_DIR}/mural"

cat > "${BIN_DIR}/mural-core" << EOF
#!/usr/bin/env bash
exec "${VENV_PYTHON}" -m mural.core.service "\$@"
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
echo "[6/6] Creating config and data directories..."
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
echo "                         rm -rf ${VENV_DIR} ${PLASMA_PLUGIN_DIR}"
echo "                         rm ${BIN_DIR}/mural ${BIN_DIR}/mural-core"
