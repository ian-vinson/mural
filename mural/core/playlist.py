# mural/core/playlist.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""Playlist data model and persistence.

Playlists are persisted to ~/.config/mural/playlists.json as a JSON array.
Each playlist has explicit monitor_assignments so the rotation timer does not
depend on monitor auto-detection succeeding.
"""

from __future__ import annotations

import json
import logging
import random
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

PLAYLISTS_FILE = Path("~/.config/mural/playlists.json").expanduser()


@dataclass
class Playlist:
    """A single named playlist.

    Attributes:
        id: UUID string; stable identifier used in D-Bus calls.
        name: Human-readable display name.
        wallpaper_paths: Ordered list of wallpaper directory paths.
        shuffle: When True, picks randomly instead of advancing in order.
        loop: When True, wraps around at the end (currently always True).
        interval_minutes: Rotation interval override; 0 = use global setting.
        monitor_assignments: Monitor output names this playlist controls.
        current_index: Index of the last-shown wallpaper (0-based).
    """

    id: str
    name: str
    wallpaper_paths: list[str] = field(default_factory=list)
    shuffle: bool = False
    loop: bool = True
    interval_minutes: int = 0
    monitor_assignments: list[str] = field(default_factory=list)
    current_index: int = 0

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "wallpaper_paths": self.wallpaper_paths,
            "shuffle": self.shuffle,
            "loop": self.loop,
            "interval_minutes": self.interval_minutes,
            "monitor_assignments": self.monitor_assignments,
            "current_index": self.current_index,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Playlist":
        return cls(
            id=d.get("id", str(uuid.uuid4())),
            name=d.get("name", "Untitled"),
            wallpaper_paths=d.get("wallpaper_paths", []),
            shuffle=bool(d.get("shuffle", False)),
            loop=bool(d.get("loop", True)),
            interval_minutes=int(d.get("interval_minutes", 0)),
            monitor_assignments=d.get("monitor_assignments", []),
            current_index=int(d.get("current_index", 0)),
        )

    # ------------------------------------------------------------------
    # Rotation
    # ------------------------------------------------------------------

    def next_wallpaper(self) -> str | None:
        """Advance the playlist and return the next valid wallpaper path.

        Filters out paths that no longer exist on disk.  Returns ``None``
        when the valid playlist is empty.  Updates :attr:`current_index`
        in-place for sequential mode.
        """
        valid = [p for p in self.wallpaper_paths if Path(p).exists()]
        if not valid:
            return None
        if self.shuffle:
            return random.choice(valid)
        # Sequential: advance and wrap
        self.current_index = (self.current_index + 1) % len(valid)
        return valid[self.current_index]

    def status_dict(self) -> dict:
        """Return a compact status dict for GetPlaylistStatus JSON output."""
        valid_count = sum(1 for p in self.wallpaper_paths if Path(p).exists())
        return {
            "id": self.id,
            "name": self.name,
            "monitors": list(self.monitor_assignments),
            "shuffle": self.shuffle,
            "interval_minutes": self.interval_minutes,
            "current_index": self.current_index,
            "total": valid_count,
        }


# ---------------------------------------------------------------------------
# PlaylistStore
# ---------------------------------------------------------------------------

class PlaylistStore:
    """Manages all playlists in memory and persists to ``playlists.json``."""

    def __init__(self) -> None:
        self._playlists: dict[str, Playlist] = {}

    def load(self) -> None:
        """Load playlists from disk; silently ignores missing file."""
        if not PLAYLISTS_FILE.exists():
            return
        try:
            raw = json.loads(PLAYLISTS_FILE.read_text(encoding="utf-8"))
            self._playlists = {p.id: p for p in (Playlist.from_dict(d) for d in raw)}
            logger.debug("Loaded %d playlist(s) from disk", len(self._playlists))
        except Exception as exc:
            logger.warning("Could not load playlists.json: %s", exc)

    def save(self) -> None:
        """Persist all playlists to disk."""
        PLAYLISTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            PLAYLISTS_FILE.write_text(
                json.dumps([p.to_dict() for p in self._playlists.values()],
                           indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("Could not save playlists.json: %s", exc)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(self, name: str) -> Playlist:
        pl = Playlist(id=str(uuid.uuid4()), name=name)
        self._playlists[pl.id] = pl
        self.save()
        return pl

    def delete(self, playlist_id: str) -> bool:
        if playlist_id not in self._playlists:
            return False
        del self._playlists[playlist_id]
        self.save()
        return True

    def get(self, playlist_id: str) -> Playlist | None:
        return self._playlists.get(playlist_id)

    def all(self) -> list[Playlist]:
        return list(self._playlists.values())

    def find_monitor_owner(self, monitor: str) -> Playlist | None:
        """Return the playlist that currently owns *monitor*, or ``None``."""
        for pl in self._playlists.values():
            if monitor in pl.monitor_assignments:
                return pl
        return None
