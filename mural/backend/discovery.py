# mural/backend/discovery.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Discovery of the linux-wallpaperengine binary and Wallpaper Engine assets.

All path searching is done lazily and cached at the module level after the
first call to :func:`discover`.  Results can be overridden via environment
variables for development and packaging purposes:

    ``MURAL_LWE_BINARY``   — absolute path to the lwe binary
    ``MURAL_ASSETS_PATH``  — absolute path to the wallpaper_engine assets dir
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_BINARY_NAME = "linux-wallpaperengine"

# Ordered list of directories to search for the lwe binary beyond PATH.
# The Mural fork of linux-wallpaperengine (github.com/ian-vinson/linux-wallpaperengine)
# provides improved wallpaper compatibility and is the recommended backend for Mural.
_BINARY_SEARCH_DIRS: tuple[str, ...] = (
    "/usr/bin",
    "/usr/local/bin",
    "/opt/linux-wallpaperengine",
    "/opt/linux-wallpaperengine/bin",
    # Mural lwe fork — local build output (development use)
    str(Path("~/Downloads/linux-wallpaperengine/build/output").expanduser()),
)

# Relative path from the Steam root to the wallpaper_engine assets directory.
_ASSETS_RELATIVE = Path("steamapps/common/wallpaper_engine")

# Ordered Steam root candidates (expanded at call-time so ~ resolves correctly).
_STEAM_ROOT_CANDIDATES: tuple[str, ...] = (
    "~/.steam/steam",
    "~/.local/share/Steam",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",
    "~/snap/steam/common/.local/share/Steam",
)


@dataclass(frozen=True)
class DiscoveryResult:
    """Result of a discovery probe.

    Attributes:
        binary: Absolute path to the ``linux-wallpaperengine`` binary,
            or ``None`` if not found.
        assets_path: Absolute path to the Wallpaper Engine assets
            directory (``steamapps/common/wallpaper_engine``), or
            ``None`` if Steam / Wallpaper Engine is not installed.
        binary_found: Convenience flag — ``True`` when ``binary`` is set.
        assets_found: Convenience flag — ``True`` when ``assets_path`` is set.
    """

    binary: Path | None
    assets_path: Path | None

    @property
    def binary_found(self) -> bool:
        """Return ``True`` if the lwe binary was located."""
        return self.binary is not None

    @property
    def assets_found(self) -> bool:
        """Return ``True`` if Wallpaper Engine assets were located."""
        return self.assets_path is not None


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------

def find_lwe_binary() -> Path | None:
    """Locate the ``linux-wallpaperengine`` binary.

    Search order:

    1. ``MURAL_LWE_BINARY`` environment variable (absolute path).
    2. ``PATH`` via :func:`shutil.which`.
    3. Well-known installation directories (AUR, opt, manual installs).

    Returns:
        Absolute :class:`~pathlib.Path` to the binary, or ``None``.
    """
    # 1. Environment variable override.
    env_override = os.environ.get("MURAL_LWE_BINARY", "").strip()
    if env_override:
        p = Path(env_override)
        if p.is_file() and os.access(p, os.X_OK):
            logger.debug("lwe binary from MURAL_LWE_BINARY: %s", p)
            return p
        logger.warning(
            "MURAL_LWE_BINARY is set to %r but the file is not executable", env_override
        )

    # 2. PATH search.
    which_result = shutil.which(_BINARY_NAME)
    if which_result:
        p = Path(which_result).resolve()
        logger.debug("lwe binary found on PATH: %s", p)
        return p

    # 3. Well-known directories.
    for directory in _BINARY_SEARCH_DIRS:
        candidate = Path(directory) / _BINARY_NAME
        if candidate.is_file() and os.access(candidate, os.X_OK):
            logger.debug("lwe binary found at %s", candidate)
            return candidate.resolve()

    logger.warning(
        "linux-wallpaperengine binary not found. "
        "Recommended: build the Mural fork for best compatibility — "
        "https://github.com/ian-vinson/linux-wallpaperengine. "
        "Standard AUR install: paru -S linux-wallpaperengine-git. "
        "Or set MURAL_LWE_BINARY to a custom binary path."
    )
    return None


# ---------------------------------------------------------------------------
# Assets discovery
# ---------------------------------------------------------------------------

def find_assets_path() -> Path | None:
    """Locate the Wallpaper Engine assets directory inside Steam.

    Search order:

    1. ``MURAL_ASSETS_PATH`` environment variable.
    2. Standard Steam installation roots (native, Flatpak, Snap).

    The assets directory contains the ``assets/`` sub-folder that
    ``linux-wallpaperengine`` passes to ``--assets-dir``.

    Returns:
        Absolute :class:`~pathlib.Path` to the assets directory, or ``None``.
    """
    # 1. Environment variable override.
    env_override = os.environ.get("MURAL_ASSETS_PATH", "").strip()
    if env_override:
        p = Path(env_override).expanduser().resolve()
        if p.is_dir():
            logger.debug("Assets path from MURAL_ASSETS_PATH: %s", p)
            return p
        logger.warning(
            "MURAL_ASSETS_PATH is set to %r but the directory does not exist", env_override
        )

    # 2. Standard Steam roots.
    for steam_root_str in _STEAM_ROOT_CANDIDATES:
        steam_root = Path(steam_root_str).expanduser()
        if not steam_root.is_dir():
            continue

        candidate = (steam_root / _ASSETS_RELATIVE).resolve()
        if candidate.is_dir():
            assets_subdir = candidate / "assets"
            if assets_subdir.is_dir():
                logger.debug("Wallpaper Engine assets found at %s", assets_subdir)
                return assets_subdir
            logger.debug("Wallpaper Engine assets found at %s", candidate)
            return candidate

    logger.info(
        "Wallpaper Engine assets directory not found. "
        "Scene-type wallpapers require Steam + Wallpaper Engine to be installed. "
        "Video and web wallpapers will work without it."
    )
    return None


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------

def discover() -> DiscoveryResult:
    """Run a full discovery probe and return a :class:`DiscoveryResult`.

    Call this once at service startup.  The result is not cached at the
    module level because the user may install lwe or Steam between
    Mural launches.

    Returns:
        A :class:`DiscoveryResult` with ``binary`` and ``assets_path``
        populated where found.
    """
    binary = find_lwe_binary()
    assets = find_assets_path()
    result = DiscoveryResult(binary=binary, assets_path=assets)

    if not result.binary_found:
        logger.error(
            "Cannot render wallpapers: linux-wallpaperengine is not installed."
        )
    if not result.assets_found:
        logger.info(
            "Wallpaper Engine assets not found; scene wallpapers will be unavailable."
        )

    return result


def require_binary() -> Path:
    """Return the lwe binary path, raising if it cannot be found.

    Convenience wrapper for call-sites that cannot proceed without lwe.

    Raises:
        FileNotFoundError: If the binary is not found.
    """
    binary = find_lwe_binary()
    if binary is None:
        raise FileNotFoundError(
            "linux-wallpaperengine binary not found. "
            "For best wallpaper compatibility, build the Mural fork: "
            "https://github.com/ian-vinson/linux-wallpaperengine. "
            "Standard install: paru -S linux-wallpaperengine-git. "
            "Or set the MURAL_LWE_BINARY environment variable."
        )
    return binary
