MURAL - README.txt
==================

Mural is an open source animated wallpaper platform for Linux.
Set video, scene, and web-based animated wallpapers on your desktop
with a community content library, session persistence, native desktop
environment integration, and Linux-native ricing tools — no terminal
required after install.

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

FEATURES (current)
-------------------

WALLPAPER PLAYBACK
- Animated wallpaper playback: video (MP4, WebM), scene-based, web-based
- Multi-monitor support with independent per-monitor wallpaper assignment
- Auto-detection of desktop environment, compositor, and session type
- Session persistence: wallpaper survives GUI close via systemd user service
- Fullscreen pause: wallpaper pauses when a fullscreen app is detected
- FPS cap, audio mute, and fullscreen pause configurable per session
- Automatic XDG_SESSION_TYPE detection for Wayland/X11 backend selection

LIBRARY
- Browse 700+ local Wallpaper Engine workshop files with thumbnail previews
- Full metadata display: title, author, resolution, file size, tags, description
- Parsed from each wallpaper's project.json automatically
- Type filter buttons: All, Video, Scene, Web, Image
- Dynamic tag chip filters: populated from your library, AND-combined with type
- Search by wallpaper name
- Clear filters button resets all filter axes at once
- Right-click wallpaper → Add to Playlist

PLAYLIST SYSTEM
- Create and name multiple playlists
- Add wallpapers from library via right-click or Add button
- Per-playlist shuffle mode (indicated with ⇌ icon)
- Per-playlist rotation interval (overrides global setting)
- Per-wallpaper duration override
- Drag-and-drop reorder within playlist
- Assign playlists to specific monitors independently
- Global auto-rotate timer with per-playlist interval support
- Playlist status shown live in Settings tab

SETTINGS
- Power profiles: Gaming, Work, Battery presets populate all settings at once
- Per-monitor playlist assignment in monitors table
- Battery auto-pause: detects AC/battery state via psutil, pauses lwe on battery
- Application rules: pause wallpaper when specified process names are running
- Coordinated pause system: battery + app rules share a pause-reasons set,
  resumes only when all pause conditions clear
- Autostart toggle: enable/disable mural-core.service systemd unit
- Re-detect monitors button

LINUX RICING INTEGRATION
- Color palette extraction: 6 dominant HEX colors extracted from preview image
  using Pillow, displayed as clickable swatches in the preview panel
- Click any swatch to copy HEX code to clipboard
- Export button copies full palette and writes
  ~/.cache/mural/current_palette.json for use in scripts, Waybar, etc.
- Pywal integration: optionally run wal on wallpaper change to theme your
  terminal, Waybar, Rofi, Dunst, and other pywal-aware applications
- Pywal status shown in Settings (detected / not found)

PERFORMANCE & STABILITY
- Process accumulation fix: hard psutil guard prevents multiple lwe instances
- Intentional stop flag: wallpaper switches don't burn through crash restart budget
- Interruptible backoff: stop/start wakes sleeping restart timer immediately
- Orphan cleanup on every start: kills any leftover lwe processes from prior crashes
- lwe stderr captured and logged to journal for debugging
- XDG_SESSION_TYPE inference: overrides logind's "unspecified" with correct value

---

PLANNED (post-v1)
-----------------
- Proper playlist editor: drag reorder, per-item duration, named playlists UI
- Hyprland / wlroots compositor support (wlr-layer-shell)
- XFCE support (X11 root window)
- GNOME Shell extension support
- Flatpak distribution via Flathub
- Creator upload portal for Mural content platform
- Audio-reactive wallpapers via PipeWire
- SteamOS / Steam Deck / TV mode optimization
- Time-of-day scheduling (morning/afternoon/evening/night profiles)
- KDE Activities integration (per-activity wallpaper)
- Virtual desktop per-wallpaper support

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
- python-gobject (system package, required for D-Bus / GLib)
- Steam + Wallpaper Engine (optional, for local Workshop file compatibility)
- python-pywal (optional, for system color scheme integration)
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

  # 2. Install system Python dependencies
  sudo pacman -S python-gobject   # Arch-based
  # or: sudo apt install python3-gi  # Debian-based

  # 3. Clone Mural
  git clone https://github.com/ian-vinson/mural.git
  cd mural

  # 4. Install
  ./install.sh

  # 5. Launch
  mural

  # Optional: pywal integration
  sudo pacman -S python-pywal
  # Enable "Apply pywal color scheme on wallpaper change" in Settings

---

HOW IT WORKS
------------
Mural has three components that work together:

1. MURAL GUI (PySide6)
   The main application window. Browse wallpapers, manage your library,
   configure playlists and settings. Closing this window does NOT stop
   your wallpaper — it keeps running via the Core Service.

2. MURAL CORE SERVICE (systemd user service)
   Runs in the background as part of your login session. Owns the wallpaper
   lifecycle — applies wallpapers to your desktop, handles multi-monitor
   assignments, pauses on fullscreen or battery, runs playlist rotation,
   and restores wallpaper on login. Communicates with the GUI over D-Bus.

3. LINUX-WALLPAPERENGINE (rendering backend)
   The C++ rendering engine by Almamu that handles the actual video/scene/web
   wallpaper rendering. Mural manages it as a subprocess — you never interact
   with it directly. Mural handles its environment, restart logic, and
   process lifecycle.

---

CONTENT
-------
Mural supports three wallpaper sources:

LOCAL FILES
  Point Mural at any folder containing MP4 videos, images, or
  linux-wallpaperengine compatible scene files via File → Add Folder.

MURAL PLATFORM
  Browse and download community wallpapers from the Platform tab (coming
  soon). Free to use. No account required to download. Account required
  to upload.

EXISTING WORKSHOP DOWNLOADS (optional)
  If you have Wallpaper Engine installed via Steam, Mural automatically
  discovers your locally downloaded Workshop files. No steamcmd required.
  Note: This uses files already on your machine only. Mural does not
  download from Steam Workshop directly.

---

ARCHITECTURE OVERVIEW
---------------------

  [Mural GUI] <──D-Bus──> [Mural Core Service] <──subprocess──> [linux-wallpaperengine]
                                  │
                       [DE Adapter Layer]
                       ├── Plasma Plugin (KDE Plasma 6)
                       ├── wlr-layer-shell (Hyprland/Sway) [planned]
                       └── X11 root window (XFCE/X11) [planned]

The Core Service exposes a D-Bus interface (org.mural.Core) with methods
for wallpaper assignment, monitor management, playlist control, settings
application, and status queries. The GUI is a thin client over this interface —
the service can run headlessly with the GUI closed.

---

RICING WORKFLOW
---------------
Mural is designed to integrate with the Linux desktop customization ecosystem:

1. Select a wallpaper in the Library tab
2. Color swatches appear in the preview panel — click any to copy HEX
3. Click Export to write ~/.cache/mural/current_palette.json
4. Use the palette in your Waybar, Hyprland, or Rofi configs:
     cat ~/.cache/mural/current_palette.json
5. Enable pywal integration in Settings → Linux Integration to
   automatically theme your entire desktop on every wallpaper change

The exported palette JSON format:
  {
    "colors": ["#1a1a2e", "#2d1b5e", "#4a3080", "#6b4ca0", "#8d6cc0", "#b09ae0"],
    "wallpaper": "/path/to/wallpaper/directory"
  }

---

CONTRIBUTING
------------
Mural is open source and welcomes contributions. Key areas needing help:

- Hyprland/wlroots adapter (wlr-layer-shell integration)
- GNOME Shell extension adapter
- XFCE X11 root window adapter
- Mural content platform backend (FastAPI / Python)
- UI/UX improvements to the PySide6 GUI
- Additional lwe compatibility testing across wallpaper types
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

- pywal by dylanaraps
  https://github.com/dylanaraps/pywal
  Color scheme generator from wallpaper images. Integrated optionally
  for system-wide theme synchronization.

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
works out of the box, persists across sessions, gives users a content library
they can browse, and integrates with the Linux ricing ecosystem.

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
- dylanaraps for pywal
- The KDE development team for Plasma 6 documentation
- The r/unixporn community for keeping desktop customization alive on Linux
