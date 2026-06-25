# mural/utils/palette.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""Color palette extraction and optional pywal integration."""

from __future__ import annotations

from pathlib import Path


def extract_palette(image_path: str, n_colors: int = 6) -> list[str]:
    """Extract *n_colors* dominant colors from *image_path*.

    Returns a list of HEX strings (e.g. ``["#1a2b3c", ...]``).
    Raises on I/O error or unsupported image format.
    """
    from PIL import Image  # local import keeps startup fast

    img = Image.open(image_path).convert("RGB")
    img.thumbnail((150, 150))
    quantized = img.quantize(colors=n_colors, method=Image.Quantize.FASTOCTREE)
    palette_data = quantized.getpalette()[:n_colors * 3]
    colors: list[str] = []
    for i in range(n_colors):
        r = palette_data[i * 3]
        g = palette_data[i * 3 + 1]
        b = palette_data[i * 3 + 2]
        colors.append(f"#{r:02x}{g:02x}{b:02x}")
    return colors


def apply_pywal(image_path: str) -> bool:
    """Run pywal on *image_path* if ``wal`` is available.

    Passes ``-n`` to skip setting the wallpaper (Mural does that itself).
    Returns ``True`` on success, ``False`` if pywal is not installed or fails.
    """
    import shutil
    import subprocess

    wal = shutil.which("wal")
    if not wal:
        return False
    result = subprocess.run(
        [wal, "--backend", "wal", "-i", image_path, "-n"],
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0
