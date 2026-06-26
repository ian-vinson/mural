# Changelog

All notable changes to Mural are documented here.

---

## [0.2.0] — 2026-06-26

### Added

**Transitions & Stability**
- Fade overlay transition: configurable black crossfade between wallpapers (`fade_overlay.py`).
- KDE sequential transition mode: eliminates Wayland surface scan-line artifacts on KWin.
- Transition mode setting in Settings → Playback: Auto / Sequential / Overlap.

**lwe Flag Coverage (full flag set now wired)**
- Volume slider 0–100 replacing the mute-only checkbox.
- No-automute option: keep audio when other apps play.
- No-audio-processing: disable audio-reactive features.
- Screen span: single wallpaper stretched across all monitors.
- Disable particles flag.
- Texture clamping mode: clamp / border / repeat.
- Granular fullscreen pause: per-monitor only mode, or ignore by app ID.
- Render debug mode (Ctrl+Shift+D developer toggle).
- Video loop mode per wallpaper: loop / no-loop / ping-pong.
- Per-wallpaper animation speed via property detection (⚡ Playback Rate label in Properties panel).
- Video hardware acceleration: Auto / NVDEC / VAAPI / Disabled.
- Process priority: Normal / Below normal / Idle (nice values).

**Pause Conditions**
- Focus/maximize pause: configurable keep/pause/stop per condition.
- Display sleep detection: stops lwe on monitor sleep via logind.
- VRAM exhaustion pause: monitors nvidia-smi / rocm-smi, configurable threshold.

**Monitoring & Integration**
- GPU memory live display in Settings → Performance.
- MPRIS now-playing: album art, title, artist in preview panel.
- OpenRGB sync: dominant palette color sent to all RGB devices on wallpaper change.
- Matugen integration: Material You theming applied on wallpaper change.
- Hyprland IPC color sync: border colors follow wallpaper palette.
- Waybar module: colored dot + wallpaper name.

**Profiles & Management**
- Multi-monitor profiles: save / load / delete named assignment sets.
- Time-of-day scheduling: four configurable wallpaper slots.
- Mass image import to playlists (folder picker + drag-and-drop from file manager).

**Infrastructure**
- `mural-cli`: full command-line interface for scripting and keybinds.
- KDE global hotkeys installer (`mural/hotkeys/`).
- SDDM lock screen screenshot on session lock (optional pkexec copy to theme dir).
- Screensaver mode: KDE screensaver `.desktop` entry installer.
- AUR PKGBUILD + `.SRCINFO` for `mural-git`.
- `pyproject.toml` packaging with hatchling.

**Library & Preview**
- Per-wallpaper scaling persistence in `~/.config/mural/wallpaper_properties.json`; ↔ indicator
  on library cards when a non-default scaling override is saved.
- Monitor resolution display (`W×H`) next to the monitor dropdown.
- PKG version compatibility detection: amber ⚠ badge on library cards and warning label in the
  preview panel for wallpapers using PKGV > 0008 scene package format.
- `usershortcut`, `separator`, `label`, and `group` property types skipped gracefully (debug log).
- Conditional properties (those with a WE `condition` field) marked with a `*` suffix and tooltip
  showing the condition expression; rendered in grey to distinguish from always-active properties.
- Version string in the window title bar (`Mural 0.2.0-alpha`).
- Help → About Mural dialog: version, rendering backend, license.

**Session Lock (SDDM)**
- Subscribes to `org.freedesktop.ScreenSaver::ActiveChanged`; captures the live wallpaper to
  `~/.local/share/mural/sddm_lock.jpg` and optionally copies it into the active SDDM theme
  directory via `pkexec` when `auto_sddm_update` is enabled.

**KDE Activities**
- D-Bus method `GetActivities() → str` (JSON array of `{id, name}` objects).
- Per-activity wallpaper assignment that switches on `CurrentActivityChanged`.

### Fixed
- Steam symlink deduplication: `~/.steam/steam → ~/.local/share/Steam` no longer produces
  duplicate library entries.
- Library grid reflow: card grid now correctly recalculates column count on window resize and
  un-maximize (was reading widget width instead of viewport width).
- lwe process accumulation: hard psutil guard + intentional-stop flag prevent orphan processes.
- KWin scan-line artifacts on wallpaper switch (sequential transition mode).
- `--scaling` flag argument order: now placed between `--screen-root` and `--bg` as lwe requires;
  previously the flag was appended after `--bg` and silently ignored.
- D-Bus service Python 3.14 + dasbus annotation incompatibility.
- `gi` (PyGObject) venv access requires `--system-site-packages`; documented in PKGBUILD.
- `clicked.disconnect()` RuntimeWarning on palette swatch re-connection (already guarded).
- Preview lwe stderr no longer floods the terminal: GLFW Wayland position warning and GLEW
  initialisation messages suppressed via daemon drain thread.

### Changed
- Speed slider removed from the preview panel; speed control is now the wallpaper's own
  Playback Rate property in the Properties panel, highlighted with ⚡ when detected.
- Version bumped to `0.2.0-alpha` in `mural/__init__.py`, `pyproject.toml`,
  `mural/core/service.py`, and `mural/main.py`.

---

## [0.1.0-alpha] — 2026-06-24

### Added

- Initial alpha release.
- `mural-core` D-Bus service managing linux-wallpaperengine (lwe) subprocess lifetime.
- GUI: Library tab (Steam Workshop + local), Platform tab (placeholder), Playlist editor,
  Settings tab.
- Per-monitor wallpaper assignment with scaling modes.
- Playlist auto-rotate with per-playlist shuffle, interval, and per-item duration overrides.
- Time-of-day schedule (four configurable slots).
- Battery pause, fullscreen detection pause, per-app process pause.
- pywal integration (primary-monitor or any-change trigger).
- MPRIS now-playing widget in preview panel; optional media metadata → lwe property passthrough.
- OpenRGB RGB sync: extracts dominant/secondary/tertiary/average palette color on wallpaper change.
- KDE Screensaver `.desktop` installer; manual SDDM background screenshot helper.
- `--screensaver` CLI flag: runs current wallpaper as a fullscreen lwe window for screensaver use.
- systemd user service autostart enable/disable from Settings.
