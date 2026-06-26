# mural/utils/matugen.py
# GPL v3 — see LICENSE

"""Matugen Material You color scheme generator integration."""

from __future__ import annotations

import json
import shutil
import subprocess


def is_available() -> bool:
    """Return True if the matugen binary is on PATH."""
    return shutil.which("matugen") is not None


def apply_matugen(image_path: str) -> bool:
    """Run ``matugen image <path>`` to generate and apply a Material You theme."""
    try:
        result = subprocess.run(
            ["matugen", "image", image_path],
            capture_output=True,
            timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


def get_matugen_colors(image_path: str) -> dict | None:
    """Run ``matugen image <path> --json hex`` and return the parsed color dict."""
    try:
        result = subprocess.run(
            ["matugen", "image", image_path, "--json", "hex"],
            capture_output=True,
            timeout=15,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except Exception:
        return None
