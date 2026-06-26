# mural/utils/thumbnail_gen.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""Generate thumbnail images for wallpapers using lwe --screenshot."""

from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_CACHE_DIR = Path("~/.cache/mural/thumbnails").expanduser()


def thumbnail_cache_path(wallpaper_path: str) -> Path:
    """Return the cache path for a wallpaper's generated thumbnail."""
    h = hashlib.sha1(wallpaper_path.encode()).hexdigest()
    return _CACHE_DIR / f"{h}.jpg"


def generate_thumbnail(
    lwe_binary: str,
    wallpaper_path: str,
    output_path: str,
    assets_dir: str | None = None,
) -> bool:
    """Run lwe --screenshot to capture a frame. Returns True on success."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [lwe_binary]
    if assets_dir:
        cmd += ["--assets-dir", assets_dir]
    cmd += [
        "--screenshot", output_path,
        "--screenshot-delay", "2",
        "--bg", wallpaper_path,
    ]
    try:
        result = subprocess.run(cmd, timeout=15, capture_output=True)
        if result.returncode == 0 and Path(output_path).exists():
            logger.debug("Generated thumbnail: %s", output_path)
            return True
        logger.debug(
            "Thumbnail generation failed (rc=%d): %s",
            result.returncode, wallpaper_path,
        )
    except subprocess.TimeoutExpired:
        logger.debug("Thumbnail generation timed out: %s", wallpaper_path)
    except FileNotFoundError:
        logger.debug("lwe binary not found for thumbnail generation")
    return False
