# mural/utils/openrgb.py
#
# Mural — Animated Wallpaper Platform for Linux
# Copyright (C) 2024  Mural Contributors
# GPL v3 — see LICENSE

"""Minimal OpenRGB SDK client (TCP protocol v3)."""

from __future__ import annotations

import logging
import socket
import struct

logger = logging.getLogger(__name__)

OPENRGB_HOST = "127.0.0.1"
OPENRGB_PORT = 6742

_MAGIC = b"ORGB"
_CMD_SET_CLIENT_NAME = 50
_CMD_GET_COUNT = 0
_CMD_GET_DEVICE_DATA = 1
_CMD_UPDATE_LEDS = 1050


def _pack_header(device_idx: int, cmd: int, data_len: int) -> bytes:
    return _MAGIC + struct.pack("<III", device_idx, cmd, data_len)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionResetError("OpenRGB connection closed")
        buf += chunk
    return buf


def _recv_packet(sock: socket.socket) -> tuple[int, int, bytes]:
    hdr = _recv_exact(sock, 16)
    if hdr[:4] != _MAGIC:
        raise ValueError(f"Invalid OpenRGB magic: {hdr[:4]!r}")
    dev_idx, cmd, data_len = struct.unpack_from("<III", hdr, 4)
    data = _recv_exact(sock, data_len) if data_len > 0 else b""
    return dev_idx, cmd, data


def _parse_string(data: bytes, pos: int) -> tuple[str, int]:
    length = struct.unpack_from("<H", data, pos)[0]
    pos += 2
    text = data[pos:pos + length].decode("utf-8", errors="replace").rstrip("\x00")
    return text, pos + length


def _skip_mode(data: bytes, pos: int) -> int:
    _, pos = _parse_string(data, pos)
    # value(i32) + flags(u32) + speed_min/max + brightness_min/max +
    # colors_min/max + speed + brightness + direction + color_mode = 13 × 4
    pos += 13 * 4
    num_colors = struct.unpack_from("<H", data, pos)[0]
    return pos + 2 + num_colors * 4


def _skip_zone(data: bytes, pos: int) -> int:
    _, pos = _parse_string(data, pos)
    pos += 4 * 4  # type, leds_min, leds_max, num_leds
    matrix_len = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    if matrix_len > 0:
        rows = struct.unpack_from("<I", data, pos)[0]
        cols = struct.unpack_from("<I", data, pos + 4)[0]
        pos += 8 + rows * cols * 4
    return pos


def _parse_led_count(data: bytes) -> int:
    """Extract the number of LEDs from a GET_DEVICE_DATA response blob."""
    try:
        pos = 0
        pos += 4  # data_size
        pos += 4  # type
        _, pos = _parse_string(data, pos)   # name
        _, pos = _parse_string(data, pos)   # description
        _, pos = _parse_string(data, pos)   # version
        _, pos = _parse_string(data, pos)   # serial
        _, pos = _parse_string(data, pos)   # location
        num_modes = struct.unpack_from("<H", data, pos)[0]; pos += 2
        for _ in range(num_modes):
            pos = _skip_mode(data, pos)
        pos += 4  # active_mode
        num_zones = struct.unpack_from("<H", data, pos)[0]; pos += 2
        for _ in range(num_zones):
            pos = _skip_zone(data, pos)
        return struct.unpack_from("<H", data, pos)[0]
    except Exception as exc:
        logger.debug("OpenRGB: LED count parse error: %s", exc)
        return 0


def is_available() -> bool:
    """Return True if OpenRGB SDK server is reachable on localhost:6742."""
    try:
        with socket.create_connection((OPENRGB_HOST, OPENRGB_PORT), timeout=1.0):
            return True
    except OSError:
        return False


def set_all_devices_color(r: int, g: int, b: int) -> bool:
    """Set all OpenRGB-managed devices to the given RGB color."""
    try:
        with socket.create_connection((OPENRGB_HOST, OPENRGB_PORT), timeout=3.0) as sock:
            # Announce ourselves
            name = b"Mural\x00"
            sock.sendall(_pack_header(0, _CMD_SET_CLIENT_NAME, len(name)) + name)

            # Get device count
            sock.sendall(_pack_header(0, _CMD_GET_COUNT, 0))
            _, _, data = _recv_packet(sock)
            count = struct.unpack_from("<I", data)[0]

            for dev_idx in range(count):
                proto_req = struct.pack("<I", 3)
                sock.sendall(
                    _pack_header(dev_idx, _CMD_GET_DEVICE_DATA, len(proto_req)) + proto_req
                )
                _, _, ctrl_data = _recv_packet(sock)

                num_leds = _parse_led_count(ctrl_data)
                if num_leds == 0:
                    continue

                color_bytes = bytes([r, g, b, 0]) * num_leds
                payload = struct.pack("<IH", 2 + len(color_bytes), num_leds) + color_bytes
                sock.sendall(_pack_header(dev_idx, _CMD_UPDATE_LEDS, len(payload)) + payload)

        return True
    except Exception as exc:
        logger.debug("OpenRGB set_color failed: %s", exc)
        return False


def set_color_from_hex(hex_color: str) -> bool:
    """Set all devices to the color represented by *hex_color* (e.g. ``"#FF8800"``)."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        return False
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return set_all_devices_color(r, g, b)
