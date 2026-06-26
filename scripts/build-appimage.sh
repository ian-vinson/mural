#!/usr/bin/env bash
# scripts/build-appimage.sh — Build a self-contained AppImage for Mural
#
# Usage:
#   bash scripts/build-appimage.sh
#
# Prerequisites (auto-downloaded to /tmp if missing):
#   appimagetool  — https://github.com/AppImage/AppImageKit
#
# The resulting AppImage is written to dist/Mural-<version>-x86_64.AppImage.
# Requires python-gobject installed on the host (gi cannot be bundled).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="/tmp/mural-appimage-build"
APPDIR="$BUILD_DIR/AppDir"
VERSION=$(python3 -c "import sys; sys.path.insert(0, '$REPO_ROOT'); from mural import __version__; print(__version__)")

echo "==> Building Mural $VERSION AppImage..."

# ---------------------------------------------------------------------------
# Ensure appimagetool is available
# ---------------------------------------------------------------------------
if ! command -v appimagetool &>/dev/null && [ ! -x /tmp/appimagetool ]; then
    echo "--> Downloading appimagetool..."
    wget -q "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage" \
        -O /tmp/appimagetool
    chmod +x /tmp/appimagetool
fi
APPIMAGETOOL=$(command -v appimagetool 2>/dev/null || echo /tmp/appimagetool)

# ---------------------------------------------------------------------------
# Clean build dir
# ---------------------------------------------------------------------------
rm -rf "$BUILD_DIR"
mkdir -p \
    "$APPDIR/usr/bin" \
    "$APPDIR/usr/lib" \
    "$APPDIR/usr/share/applications" \
    "$APPDIR/usr/share/icons/hicolor/256x256/apps" \
    "$APPDIR/usr/share/metainfo"

# ---------------------------------------------------------------------------
# Python venv inside AppDir (--system-site-packages so gi is reachable)
# ---------------------------------------------------------------------------
echo "--> Creating Python venv..."
python3 -m venv --system-site-packages "$APPDIR/usr/python"
VENV_PIP="$APPDIR/usr/python/bin/pip"
VENV_PYTHON="$APPDIR/usr/python/bin/python3"

echo "--> Installing Python dependencies..."
"$VENV_PIP" install --quiet --upgrade pip
"$VENV_PIP" install --quiet \
    PySide6 dasbus requests Pillow psutil watchdog
"$VENV_PIP" install --quiet "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Entry point wrappers
# ---------------------------------------------------------------------------
cat > "$APPDIR/usr/bin/mural" << 'WRAPPER'
#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
exec "$HERE/../python/bin/python3" -m mural.main "$@"
WRAPPER
chmod +x "$APPDIR/usr/bin/mural"

cat > "$APPDIR/usr/bin/mural-cli" << 'WRAPPER'
#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
exec "$HERE/../python/bin/python3" -m mural.cli "$@"
WRAPPER
chmod +x "$APPDIR/usr/bin/mural-cli"

# ---------------------------------------------------------------------------
# Desktop entry
# ---------------------------------------------------------------------------
cat > "$APPDIR/usr/share/applications/mural.desktop" << 'DESKTOP'
[Desktop Entry]
Type=Application
Name=Mural
GenericName=Animated Wallpaper
Comment=Animated wallpaper platform for Linux
Exec=mural %u
Icon=mural
Categories=Settings;DesktopSettings;
Keywords=wallpaper;animated;background;linux;ricing;
StartupNotify=true
DESKTOP

# ---------------------------------------------------------------------------
# Icon — use existing asset or generate a minimal placeholder
# ---------------------------------------------------------------------------
ICON_OUT="$APPDIR/usr/share/icons/hicolor/256x256/apps/mural.png"
if [ -f "$REPO_ROOT/mural/assets/mural.png" ]; then
    cp "$REPO_ROOT/mural/assets/mural.png" "$ICON_OUT"
else
    echo "--> Generating placeholder icon..."
    python3 << PYICON
from PIL import Image, ImageDraw
img = Image.new('RGBA', (256, 256), (26, 26, 46, 255))
draw = ImageDraw.Draw(img)
draw.ellipse([24, 24, 232, 232], fill=(91, 138, 247, 255))
# Simple 'M' letterform via polygons
pts = [
    (68, 188), (68, 88), (96, 88), (128, 138),
    (160, 88), (188, 88), (188, 188), (164, 188),
    (164, 122), (138, 168), (118, 168), (92, 122),
    (92, 188),
]
draw.polygon(pts, fill=(255, 255, 255, 240))
img.save('$ICON_OUT')
PYICON
fi

# ---------------------------------------------------------------------------
# AppStream metainfo
# ---------------------------------------------------------------------------
cat > "$APPDIR/usr/share/metainfo/io.github.ian_vinson.Mural.metainfo.xml" << 'META'
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>io.github.ian_vinson.Mural</id>
  <name>Mural</name>
  <summary>Animated wallpaper platform for Linux</summary>
  <description>
    <p>Mural is an open source animated wallpaper platform for Linux.
    Set video, scene, and web-based animated wallpapers on your desktop
    with session persistence, multi-monitor support, and native Linux
    ricing integrations including pywal, matugen, Hyprland IPC, and
    OpenRGB.</p>
  </description>
  <url type="homepage">https://github.com/ian-vinson/mural</url>
  <url type="bugtracker">https://github.com/ian-vinson/mural/issues</url>
  <releases>
    <release version="0.2.0" date="2026-06-26"/>
  </releases>
  <content_rating type="oars-1.1"/>
</component>
META

# ---------------------------------------------------------------------------
# AppRun — entry point executed by the AppImage runtime
# ---------------------------------------------------------------------------
cat > "$APPDIR/AppRun" << 'APPRUN'
#!/bin/bash
HERE="$(dirname "$(readlink -f "${0}")")"
PYTHON="$HERE/usr/python/bin/python3"

# gi (PyGObject) cannot be bundled — it must come from the host system.
# The venv was created with --system-site-packages so gi is reachable
# as long as python-gobject is installed on the host.
if ! "$PYTHON" -c "import gi" 2>/dev/null; then
    MSG="Mural requires python-gobject (gi) from your system.\n\nInstall it with:\n  sudo pacman -S python-gobject\n\nor on Ubuntu/Debian:\n  sudo apt install python3-gi"
    zenity --error --title="Mural — Missing Dependency" --text="$MSG" 2>/dev/null || \
        echo -e "ERROR: $MSG" >&2
    exit 1
fi

exec "$PYTHON" -m mural.main "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

# ---------------------------------------------------------------------------
# Symlinks required by AppImage spec (must sit at AppDir root)
# ---------------------------------------------------------------------------
ln -sf usr/share/applications/mural.desktop "$APPDIR/mural.desktop"
ln -sf usr/share/icons/hicolor/256x256/apps/mural.png "$APPDIR/mural.png"

# ---------------------------------------------------------------------------
# Build the AppImage
# ---------------------------------------------------------------------------
mkdir -p "$REPO_ROOT/dist"
echo "--> Running appimagetool..."
ARCH=x86_64 "$APPIMAGETOOL" --no-appstream "$APPDIR" \
    "$REPO_ROOT/dist/Mural-${VERSION}-x86_64.AppImage"

echo ""
echo "==> Built: dist/Mural-${VERSION}-x86_64.AppImage"
echo ""
echo "Test with:"
echo "  chmod +x dist/Mural-${VERSION}-x86_64.AppImage"
echo "  ./dist/Mural-${VERSION}-x86_64.AppImage"
echo ""
echo "Note: linux-wallpaperengine and python-gobject must be installed on the host."
