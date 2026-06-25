# mural/config.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""Centralised configuration management for Mural.

All settings are stored under ``~/.config/mural/``.  The module exposes a
single :class:`MuralConfig` object that loads on first access and can be
saved back to disk at any time.  Other modules import :data:`config` and
read/write attributes on it.

File layout::

    ~/.config/mural/
        settings.json    — GUI / playback preferences
        monitors.json    — per-monitor wallpaper assignments
        library.json     — user-added library directories
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_DIR = Path("~/.config/mural").expanduser()
SETTINGS_FILE = CONFIG_DIR / "settings.json"
MONITORS_FILE = CONFIG_DIR / "monitors.json"
LIBRARY_FILE  = CONFIG_DIR / "library.json"

DOWNLOAD_DIR = Path("~/.local/share/mural/downloads").expanduser()
CACHE_DIR    = Path("~/.cache/mural").expanduser()

_DEFAULTS: dict[str, Any] = {
    # Playback
    "fps_limit": 30,
    "mute_audio": False,
    "pause_on_battery": True,
    "fullscreen_pause": True,
    # Performance
    "quality_profile": "Medium",
    # Playlist
    "playlist_interval_minutes": 0,
    # Autostart
    "autostart": True,
    # Library
    "extra_library_dirs": [],
    # Platform
    "platform_api_url": "https://api.mural.app/v1",
    "platform_page_size": 24,
}


class MuralConfig:
    """Persistent configuration backed by ``~/.config/mural/settings.json``.

    Attributes are read/written directly; call :meth:`save` to persist.
    """

    def __init__(self) -> None:
        self._data: dict[str, Any] = dict(_DEFAULTS)
        self._loaded = False

    def load(self) -> "MuralConfig":
        """Load settings from disk.  Missing keys fall back to defaults.

        Returns:
            Self, for chaining.
        """
        if SETTINGS_FILE.exists():
            try:
                on_disk = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                self._data = {**_DEFAULTS, **on_disk}
            except Exception as exc:
                logger.warning("Could not read settings.json (%s) — using defaults", exc)
        self._loaded = True
        return self

    def save(self) -> None:
        """Persist the current settings to ``~/.config/mural/settings.json``."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            SETTINGS_FILE.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("Could not save settings.json: %s", exc)

    # ------------------------------------------------------------------
    # Attribute-style access
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if not self._loaded:
            self.load()
        if name in self._data:
            return self._data[name]
        raise AttributeError(f"No config key: {name!r}")

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            super().__setattr__(name, value)
        else:
            self._data[name] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Return the config value for *key*, or *default*."""
        return self._data.get(key, default)

    def as_dict(self) -> dict[str, Any]:
        """Return a shallow copy of the full settings dict."""
        return dict(self._data)

    # ------------------------------------------------------------------
    # Directory helpers
    # ------------------------------------------------------------------

    @staticmethod
    def ensure_dirs() -> None:
        """Create required application directories if they do not exist."""
        for d in (CONFIG_DIR, DOWNLOAD_DIR, CACHE_DIR):
            d.mkdir(parents=True, exist_ok=True)


# Module-level singleton — import and use directly:
#   from mural.config import config
#   config.fps_limit = 60
#   config.save()
config = MuralConfig()
