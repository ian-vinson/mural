#!/usr/bin/env bash
# install-hotkeys.sh — Register Mural as a KDE global shortcut component.
#
# Run this script once to install the Mural shortcut component. After running
# it, open System Settings → Keyboard → Shortcuts and assign keys under
# "Mural Wallpaper Controls".

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS_DIR="${HOME}/.local/share/applications"

DESKTOP_SRC="${SCRIPT_DIR}/mural-hotkeys.desktop"

echo "Installing Mural keyboard shortcut component..."

# Install the .desktop file
mkdir -p "${APPS_DIR}"
cp "${DESKTOP_SRC}" "${APPS_DIR}/mural-hotkeys.desktop"
echo "  Installed: ${APPS_DIR}/mural-hotkeys.desktop"

# Notify KDE to rescan .desktop files so the new component appears immediately
if command -v kbuildsycoca6 &>/dev/null; then
    kbuildsycoca6 --noincremental &>/dev/null || true
    echo "  Ran kbuildsycoca6"
elif command -v kbuildsycoca5 &>/dev/null; then
    kbuildsycoca5 --noincremental &>/dev/null || true
    echo "  Ran kbuildsycoca5"
fi

echo ""
echo "Done.  To assign shortcut keys:"
echo "  System Settings → Keyboard → Shortcuts → Mural Wallpaper Controls"
echo ""
echo "Available actions:"
echo "  Pause/Resume Wallpaper  →  mural-cli toggle"
echo "  Next Wallpaper          →  mural-cli next"
echo "  Random Wallpaper        →  mural-cli random"
