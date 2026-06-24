MURAL - README.txt
==================

Mural is an open source animated wallpaper platform for Linux.
Set video, scene, and web-based animated wallpapers on your desktop
with a community content library, session persistence, and native
desktop environment integration — no terminal required after install.

Built for the Linux desktop Wallpaper Engine always refused to support.

---

PROJECT STATUS
--------------
Version: 0.1.0-alpha (pre-release, active development)
Primary Target: KDE Plasma 6 (Wayland + X11)
Rendering Backend: linux-wallpaperengine by Almamu
GUI Framework: Python 3.11+ / PySide6
License: GPL v3

---

FEATURES (v1 Roadmap)
----------------------
- Animated wallpaper playback: video (MP4, WebM), scene-based, web-based
- Native KDE Plasma integration via wallpaper plugin (no terminal to apply)
- Session persistence: wallpaper survives GUI close via systemd user service
- Multi-monitor support with per-monitor wallpaper assignment
- Auto-detection of desktop environment and compositor
- Mural content platform: browse and download community wallpapers
- Local file support: use any MP4, image, or compatible scene file
- Fullscreen pause: wallpaper pauses when a fullscreen app is detected
- Playlist support: rotate wallpapers on a schedule
- Resolution support: 1080p, 1440p, 2K, 4K and ultrawide formats
- AUR package for Arch-based distros (CachyOS, Manjaro, EndeavourOS)

PLANNED (post-v1)
- Hyprland / wlroots compositor support (wlr-layer-shell)
- XFCE support (X11 root window)
- GNOME Shell extension support
- Flatpak distribution via Flathub
- Creator upload portal for Mural content platform
- Audio-reactive wallpapers via PipeWire
- SteamOS / Steam Deck / TV mode optimization

---

SUPPORTED DESKTOP ENVIRONMENTS
-------------------------------
v1 (current):
  KDE Plasma 6       SUPPORTED (Wayland + X11)

Planned:
  Hyprland           IN PROGRESS
  Sway               PLANNED
  XFCE               PLANNED
  GNOME              PLANNED (technically difficult, lower priority)

Auto-detection: Mural detects your DE automatically via $XDG_CURRENT_DESKTOP
and $XDG_SESSION_TYPE at launch. No manual configuration required.

---

REQUIREMENTS
------------
- Linux (Arch-based recommended for v1)
- KDE Plasma 6
- Python 3.11 or higher
- linux-wallpaperengine (installed automatically as dependency)
- Steam + Wallpaper Engine (optional, for local Workshop file compatibility)
- Internet connection (optional, for Mural content platform)

---

INSTALLATION
------------

AUR (Arch, CachyOS, Manjaro, EndeavourOS):
  paru -S mural-git
  or
  yay -S mural-git

This automatically installs linux-wallpaperengine as a dependency.

Manual (any distro):
  # 1. Install linux-wallpaperengine first
  # See https://github.com/Almamu/linux-wallpaperengine

  # 2. Clone Mural
  git clone https://github.com/ian-vinson/mural.git
  cd mural

  # 3. Install Python dependencies
  pip install -r requirements.txt

  # 4. Install the systemd user service
  ./install.sh

  # 5. Launch
  mural

---

HOW IT WORKS
------------
Mural has three components that work together:

1. MURAL GUI (PySide6)
   The main application window. Browse wallpapers, manage your library,
   configure settings. Closing this window does NOT stop your wallpaper.

2. MURAL CORE SERVICE (systemd user service)
   Runs in the background as part of your login session. Owns the wallpaper
   lifecycle — applies wallpapers to your desktop, handles multi-monitor
   assignments, pauses on fullscreen, restores on login. This is what makes
   wallpapers persist after closing the GUI.

3. LINUX-WALLPAPERENGINE (rendering backend)
   The C++ rendering engine by Almamu that handles the actual video/scene/web
   wallpaper rendering. Mural manages it — you never interact with it directly.

---

CONTENT
-------
Mural supports three wallpaper sources:

LOCAL FILES
  Point Mural at any folder containing MP4 videos, images, or
  linux-wallpaperengine compatible scene files.

MURAL PLATFORM
  Browse and download community wallpapers from mural.app (coming soon).
  Free to use. No account required to download. Account required to upload.

EXISTING WORKSHOP DOWNLOADS (optional)
  If you have Wallpaper Engine installed via Steam, Mural can browse
  your locally downloaded Workshop files. No steamcmd required.
  Note: This uses files already on your machine only. Mural does not
  download from Steam Workshop.

---

ARCHITECTURE OVERVIEW
---------------------

  [Mural GUI] <──IPC──> [Mural Core Service] <──subprocess──> [linux-wallpaperengine]
                               │
                    [DE Adapter Layer]
                    ├── Plasma Plugin (KDE Plasma 6)
                    ├── wlr-layer-shell (Hyprland/Sway) [planned]
                    └── X11 root window (XFCE/X11) [planned]

The DE Adapter Layer is the key innovation in Mural's design. The rendering
backend (linux-wallpaperengine) is DE-agnostic. The adapter layer handles the
DE-specific integration needed to make the rendered output appear correctly as
a desktop background — natively integrated, session-managed, and persistent.

---

CONTRIBUTING
------------
Mural is open source and welcomes contributions. Key areas needing help:

- Hyprland/wlroots adapter (wlr-layer-shell integration)
- GNOME Shell extension adapter
- XFCE X11 root window adapter
- Mural content platform backend (FastAPI / Python)
- UI/UX improvements to the PySide6 GUI
- Wallpaper format documentation and testing

To contribute:
  git clone https://github.com/ian-vinson/mural.git
  cd mural
  pip install -r requirements-dev.txt
  # See DEVGUIDE.txt for full development setup

---

RELATED PROJECTS
----------------
Mural builds on and is inspired by:

- linux-wallpaperengine by Almamu
  https://github.com/Almamu/linux-wallpaperengine
  The rendering backend Mural uses. Excellent project.

- wallpaper-engine-kde-plugin by catsout / CaptSilver
  https://github.com/catsout/wallpaper-engine-kde-plugin
  Plasma 6 wallpaper plugin reference implementation. Studied for Mural's
  KDE adapter design.

- Variety Wallpaper Changer
  https://github.com/varietywalls/variety
  Static wallpaper manager. The gap between Variety and Wallpaper Engine
  is exactly what Mural aims to fill.

---

WHY MURAL?
----------
Wallpaper Engine is the gold standard for animated desktop wallpapers.
It has 30 million users on Windows and explicitly refuses to support Linux
citing market size and cross-DE complexity.

The Linux desktop community has produced pieces of this puzzle:
linux-wallpaperengine renders scenes, various GUIs wrap the CLI, KDE plugins
bridge the DE gap. But nothing assembles these pieces into a product that
works out of the box, persists across sessions, and gives users a content
library they can browse and download from.

Mural is that product.

---

LICENSE
-------
Mural is licensed under the GNU General Public License v3.0.
See LICENSE file for details.

The Mural content platform API is licensed under MIT.
See platform/LICENSE for details.

---

ACKNOWLEDGEMENTS
----------------
- Almamu and contributors to linux-wallpaperengine
- catsout and CaptSilver for wallpaper-engine-kde-plugin
- The KDE development team for Plasma 6 documentation
- The r/unixporn community for keeping desktop customization alive on Linux
