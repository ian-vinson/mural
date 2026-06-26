#!/usr/bin/env python3
# mural/waybar/mural-waybar.py
# GPL v3 — see LICENSE
"""
Mural Waybar module.

Add to your Waybar config:
  "custom/mural": {
      "exec": "~/.local/share/mural/waybar/mural-waybar.py",
      "interval": 5,
      "format": "{}",
      "tooltip": true,
      "return-type": "json"
  }
"""

import json
import sys
from pathlib import Path

PALETTE_FILE = Path("~/.cache/mural/current_palette.json").expanduser()

_EMPTY = json.dumps({"text": "", "tooltip": "", "class": ""})


def main() -> None:
    if not PALETTE_FILE.exists():
        print(_EMPTY)
        return

    try:
        data = json.loads(PALETTE_FILE.read_text(encoding="utf-8"))
    except Exception:
        print(_EMPTY)
        return

    colors = data.get("colors", [])
    wallpaper = data.get("wallpaper", "")
    name = data.get("name", Path(wallpaper).name if wallpaper else "")

    dominant = colors[0] if colors else "#888888"

    dot = f'<span color="{dominant}">⬤</span>'
    short_name = name[:30] + ("…" if len(name) > 30 else "")

    swatch_line = "  ".join(
        f'<span color="{c}">⬤</span> {c}' for c in colors[:6]
    )

    output = {
        "text": f"{dot} {short_name}",
        "tooltip": f"<b>{name}</b>\n{swatch_line}\n{wallpaper}",
        "class": "mural",
        "percentage": 0,
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
