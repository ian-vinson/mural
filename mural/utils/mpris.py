# mural/utils/mpris.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""MPRIS2 media player query utility."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class MediaInfo:
    title: str = ""
    artist: str = ""
    album: str = ""
    art_url: str = ""
    playing: bool = False


def get_current_media() -> MediaInfo | None:
    """Query MPRIS2 D-Bus for the currently playing track.

    Returns the first service that reports PlaybackStatus=Playing, or None.
    """
    try:
        import gi
        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib

        conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        res = conn.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "ListNames",
            None,
            GLib.VariantType.new("(as)"),
            Gio.DBusCallFlags.NONE,
            2000,
            None,
        )
        names = res.get_child_value(0).unpack()
        mpris_names = [n for n in names if n.startswith("org.mpris.MediaPlayer2.")]

        for service_name in mpris_names:
            try:
                props_res = conn.call_sync(
                    service_name,
                    "/org/mpris/MediaPlayer2",
                    "org.freedesktop.DBus.Properties",
                    "GetAll",
                    GLib.Variant("(s)", ("org.mpris.MediaPlayer2.Player",)),
                    GLib.VariantType.new("(a{sv})"),
                    Gio.DBusCallFlags.NONE,
                    2000,
                    None,
                )
                props = props_res.get_child_value(0).unpack()
                status = str(props.get("PlaybackStatus", "Stopped"))
                if status == "Playing":
                    metadata = props.get("Metadata") or {}
                    title = str(metadata.get("xesam:title", ""))
                    raw_artists = metadata.get("xesam:artist") or []
                    artist = ", ".join(str(a) for a in raw_artists)
                    album = str(metadata.get("xesam:album", ""))
                    art_url = str(metadata.get("mpris:artUrl", ""))
                    return MediaInfo(
                        title=title,
                        artist=artist,
                        album=album,
                        art_url=art_url,
                        playing=True,
                    )
            except Exception:
                continue

        return None
    except Exception as exc:
        logger.debug("MPRIS query failed: %s", exc)
        return None
