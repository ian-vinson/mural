# mural/core/profiles.py
# GPL v3 — see LICENSE

"""Multi-monitor wallpaper profile storage."""

from __future__ import annotations

import datetime
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

PROFILES_FILE = Path("~/.config/mural/profiles.json").expanduser()


@dataclass
class MonitorProfile:
    """A named snapshot of monitor→wallpaper assignments.

    Attributes:
        id: UUID string.
        name: Human-readable profile name.
        assignments: Mapping of monitor name → wallpaper path.
        scaling: Mapping of monitor name → scaling mode string.
        created_at: ISO-8601 timestamp string.
    """

    id: str
    name: str
    assignments: dict[str, str]
    scaling: dict[str, str]
    created_at: str


class ProfileStore:
    """Persistent store for :class:`MonitorProfile` objects."""

    def __init__(self) -> None:
        self._profiles: list[MonitorProfile] = []

    def load(self) -> list[MonitorProfile]:
        """Load profiles from disk; returns empty list on error."""
        if not PROFILES_FILE.exists():
            self._profiles = []
            return []
        try:
            raw: list[dict] = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
            self._profiles = [
                MonitorProfile(
                    id=p["id"],
                    name=p["name"],
                    assignments=dict(p.get("assignments", {})),
                    scaling=dict(p.get("scaling", {})),
                    created_at=p.get("created_at", ""),
                )
                for p in raw
                if isinstance(p, dict) and "id" in p
            ]
        except Exception:
            self._profiles = []
        return list(self._profiles)

    def save(self, profiles: list[MonitorProfile]) -> None:
        """Persist *profiles* to disk."""
        PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROFILES_FILE.write_text(
            json.dumps(
                [
                    {
                        "id": p.id,
                        "name": p.name,
                        "assignments": p.assignments,
                        "scaling": p.scaling,
                        "created_at": p.created_at,
                    }
                    for p in profiles
                ],
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self._profiles = list(profiles)

    def create(
        self,
        name: str,
        assignments: dict[str, str],
        scaling: dict[str, str],
    ) -> MonitorProfile:
        """Create a new profile, persist it, and return it."""
        profile = MonitorProfile(
            id=str(uuid.uuid4()),
            name=name,
            assignments=dict(assignments),
            scaling=dict(scaling),
            created_at=datetime.datetime.now().isoformat(timespec="seconds"),
        )
        self._profiles.append(profile)
        self.save(self._profiles)
        return profile

    def delete(self, profile_id: str) -> bool:
        """Delete profile by ID; returns True if found and removed."""
        before = len(self._profiles)
        self._profiles = [p for p in self._profiles if p.id != profile_id]
        if len(self._profiles) < before:
            self.save(self._profiles)
            return True
        return False

    def get(self, profile_id: str) -> MonitorProfile | None:
        return next((p for p in self._profiles if p.id == profile_id), None)

    def all(self) -> list[MonitorProfile]:
        return list(self._profiles)
