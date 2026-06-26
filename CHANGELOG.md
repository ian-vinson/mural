# Changelog

All notable changes to Mural are documented here.

---

## [0.2.0-alpha] — 2026-06-25

### Added

**Transition Fade Overlay** (`mural/gui/fade_overlay.py`)
- New `FadeOverlay` widget: frameless, top-most black overlay that fades to opaque then back to
  transparent when switching wallpapers, masking the raw cut between two lwe instances.
- `_PreviewPanel` creates one `FadeOverlay` instance at startup and calls `do_transition()` before
  every `SetWallpaper` D-Bus call.
- Settings → Playback: "Fade transition when switching wallpapers" checkbox (`fade_transition`,
  default `true`) and "Duration" spinbox (`fade_duration_ms`, default `400 ms`).

**Session Lock Auto-Screenshot for SDDM** (`mural/core/service.py`)
- Service now subscribes to `org.freedesktop.ScreenSaver::ActiveChanged` via GIO D-Bus signals at
  startup.
- On screen lock: captures the current wallpaper to `~/.local/share/mural/sddm_lock.jpg` using
  `lwe --screenshot --screenshot-delay 0`.
- If `auto_sddm_update` is enabled and an SDDM theme is detected in `/etc/sddm.conf`, runs
  `pkexec cp <src> /usr/share/sddm/themes/<theme>/background.jpg` automatically.
- Settings → Screensaver: "Auto-update SDDM background when screen locks (requires pkexec)"
  checkbox (`auto_sddm_update`, default `false`).

**KDE Activities Integration** (`mural/core/service.py`, `mural/gui/settings_tab.py`)
- New D-Bus method `GetActivities() -> str`: returns a JSON array `[{id, name}]` by querying
  `org.kde.ActivityManager.Activities.{ListActivities,ActivityName}`.  Returns `[]` on
  non-Plasma desktops.
- Service subscribes to `org.kde.ActivityManager.Activities::CurrentActivityChanged` on Plasma.
  When the active activity changes and `activity_sync_enabled` is `true`, the wallpaper assigned
  to that activity in `activity_wallpapers` config is applied to all monitors.
- Settings → KDE Activities: enable/disable checkbox, per-activity wallpaper pickers with
  "Refresh activities" button, populated from the live service.

**Mass Image Import to Playlist** (`mural/gui/playlist_tab.py`)
- New `_DnDList(QListWidget)` subclass with `items_dropped = Signal(list)`: accepts external
  file/URL drops from the file manager (via `text/uri-list`) while preserving internal drag-and-drop
  reordering.
- The playlist wallpaper list now uses `_DnDList` — items can be dragged in from Dolphin or any
  other file manager.
- "Import Images" button: opens a directory picker, recursively scans for `*.jpg *.jpeg *.png
  *.gif *.webp *.bmp`, shows a count confirmation dialog, then batch-adds all files via
  `AddToPlaylist()`.

### Changed

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
