# mural/core/playlist_store.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""Persist the user playlist to ~/.config/mural/playlist.json."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PLAYLIST_FILE = Path("~/.config/mural/playlist.json").expanduser()

_DEFAULTS: dict = {
    "items": [],
    "shuffle": False,
    "current_index": 0,
}


def load() -> dict:
    """Load playlist from disk; missing keys fall back to defaults."""
    if not PLAYLIST_FILE.exists():
        return dict(_DEFAULTS)
    try:
        data = json.loads(PLAYLIST_FILE.read_text(encoding="utf-8"))
        return {**_DEFAULTS, **data}
    except Exception as exc:
        logger.warning("Could not read playlist.json (%s) — using defaults", exc)
        return dict(_DEFAULTS)


def save(data: dict) -> None:
    """Persist *data* to playlist.json."""
    PLAYLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        PLAYLIST_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.error("Could not save playlist.json: %s", exc)
