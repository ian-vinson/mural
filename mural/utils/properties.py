# mural/utils/properties.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""Wallpaper Engine scene property parsing and per-wallpaper override storage.

Scene wallpapers define user-configurable properties in ``project.json``
(rain on/off, fog intensity, bloom, color schemes, etc.).  lwe accepts
``--set-property key=value`` at launch to override them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROPS_FILE = Path("~/.config/mural/wallpaper_properties.json").expanduser()

# Maps project.json type strings to our canonical type names.
_TYPE_MAP: dict[str, str] = {
    "bool":      "bool",
    "slider":    "slider",
    "color":     "color",
    "combolist": "combo",
    "textinput": "text",
    "integer":   "slider",
    "double":    "slider",
}

# Property types that are UI/display-only in Wallpaper Engine — skip silently.
_SKIP_PROP_TYPES: frozenset[str] = frozenset({
    "group",        # section header, no value
    "usershortcut", # keyboard binding metadata
    "separator",    # horizontal rule in WE UI
    "label",        # static display text
})


@dataclass
class WallpaperProperty:
    """A single user-configurable scene property.

    Attributes:
        key:     Property key used with ``--set-property key=value``.
        label:   Human-readable display name.
        type:    ``"bool"``, ``"slider"``, ``"color"``, ``"combo"``, or ``"text"``.
        value:   Default value as a string.
        min_val: Minimum value for slider type.
        max_val: Maximum value for slider type.
        step:    Step increment for slider type.
        options: Ordered option labels for combo type.
    """

    key: str
    label: str
    type: str
    value: str
    min_val: float = 0.0
    max_val: float = 1.0
    step: float = 0.1
    options: list[str] = field(default_factory=list)
    condition: str = ""


def parse_properties(project_json_path: str) -> list[WallpaperProperty]:
    """Parse user-configurable properties from *project_json_path*.

    Returns an empty list on any error or if no properties are defined.
    """
    try:
        data = json.loads(Path(project_json_path).read_text(encoding="utf-8"))
    except Exception:
        return []

    raw: Any = data.get("general", {}).get("properties", {})
    if not isinstance(raw, dict):
        return []

    props: list[WallpaperProperty] = []
    for key, item in raw.items():
        if not isinstance(item, dict):
            continue
        prop_type = item.get("type", "").lower()
        if not prop_type:
            logger.debug("Skipping property with no type: %s", key)
            continue
        if prop_type in _SKIP_PROP_TYPES:
            logger.debug("Skipping UI-only property type %r: %s", prop_type, key)
            continue
        mapped = _TYPE_MAP.get(prop_type)
        if mapped is None:
            logger.debug("Unknown property type %r for %s — skipping", prop_type, key)
            continue

        label = item.get("text") or key
        raw_val = item.get("value", "")

        # Convert the default value to a canonical string.
        if mapped == "bool":
            val_str = "1" if (
                raw_val is True or raw_val == 1
                or str(raw_val).lower() in ("true", "1")
            ) else "0"
        elif mapped == "slider":
            try:
                val_str = str(float(raw_val))
            except (TypeError, ValueError):
                val_str = "0.0"
        elif mapped == "color":
            val_str = _color_to_hex(raw_val)
        elif mapped == "combo":
            try:
                val_str = str(int(raw_val))
            except (TypeError, ValueError):
                val_str = "0"
        else:
            val_str = str(raw_val) if raw_val is not None else ""

        # Numeric range / step for sliders.
        min_val = float(item.get("min", 0.0))
        max_val = float(item.get("max", 1.0))
        precision = int(item.get("precision", 2))
        raw_step = item.get("step")
        try:
            step = float(raw_step) if raw_step is not None else 10 ** (-max(0, precision))
        except (TypeError, ValueError):
            step = 0.01
        step = max(step, 1e-6)

        # Options for combo.
        options: list[str] = []
        for opt in (item.get("options") or []):
            if isinstance(opt, dict):
                options.append(str(opt.get("label") or opt.get("value") or ""))
            else:
                options.append(str(opt))

        props.append(WallpaperProperty(
            key=key,
            label=label,
            type=mapped,
            value=val_str,
            min_val=min_val,
            max_val=max_val,
            step=step,
            options=options,
            condition=str(item.get("condition", "")),
        ))

    order_map = {
        k: v.get("order", 999)
        for k, v in raw.items()
        if isinstance(v, dict)
    }
    props.sort(key=lambda p: order_map.get(p.key, 999))
    return props


def _color_to_hex(value: Any) -> str:
    """Normalise a project.json color value to ``#rrggbb`` hex string."""
    if isinstance(value, str):
        value = value.strip()
        if value.startswith("#"):
            return value
        parts = value.split()
        if len(parts) == 3:
            try:
                r, g, b = (int(float(p) * 255) for p in parts)
                return f"#{r:02x}{g:02x}{b:02x}"
            except (ValueError, TypeError):
                pass
    return "#ffffff"


def has_properties(wallpaper_path: str) -> bool:
    """Return ``True`` if the wallpaper directory has user-configurable properties."""
    proj = Path(wallpaper_path) / "project.json"
    if not proj.exists():
        return False
    try:
        data = json.loads(proj.read_text(encoding="utf-8"))
        return bool(data.get("general", {}).get("properties"))
    except Exception:
        return False


def load_overrides(wallpaper_path: str) -> dict[str, str]:
    """Return saved property overrides for *wallpaper_path* (empty dict if none)."""
    if not PROPS_FILE.exists():
        return {}
    try:
        data = json.loads(PROPS_FILE.read_text(encoding="utf-8"))
        return {k: str(v) for k, v in data.get(wallpaper_path, {}).items()}
    except Exception:
        return {}


def save_overrides(wallpaper_path: str, overrides: dict[str, str]) -> None:
    """Persist *overrides* for *wallpaper_path* to the shared overrides file."""
    data: dict = {}
    if PROPS_FILE.exists():
        try:
            data = json.loads(PROPS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    if overrides:
        data[wallpaper_path] = overrides
    else:
        data.pop(wallpaper_path, None)
    PROPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROPS_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
