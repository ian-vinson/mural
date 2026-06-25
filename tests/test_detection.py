# tests/test_detection.py
#
# Mural — Animated Wallpaper Platform for Linux
# GPL v3 — see LICENSE

"""Tests for mural/detection.py — DE and compositor auto-detection."""

import pytest

from mural.detection import (
    DetectionResult,
    _detect_desktop,
    _detect_session,
    _read_env,
    detect,
)


# ---------------------------------------------------------------------------
# Session type detection
# ---------------------------------------------------------------------------

class TestDetectSession:
    def test_wayland_explicit(self):
        env = {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "", "DISPLAY": ""}
        assert _detect_session(env) == "wayland"

    def test_x11_explicit(self):
        env = {"XDG_SESSION_TYPE": "x11", "WAYLAND_DISPLAY": "", "DISPLAY": ":0"}
        assert _detect_session(env) == "x11"

    def test_wayland_via_display_var(self):
        env = {"XDG_SESSION_TYPE": "", "WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ""}
        assert _detect_session(env) == "wayland"

    def test_x11_via_display_var(self):
        env = {"XDG_SESSION_TYPE": "", "WAYLAND_DISPLAY": "", "DISPLAY": ":1"}
        assert _detect_session(env) == "x11"

    def test_fallback_to_x11(self):
        env = {"XDG_SESSION_TYPE": "", "WAYLAND_DISPLAY": "", "DISPLAY": ""}
        assert _detect_session(env) == "x11"


# ---------------------------------------------------------------------------
# Desktop detection
# ---------------------------------------------------------------------------

class TestDetectDesktop:
    def _env(self, xdg="", plasma="", kde="", hypr="", gnome=""):
        return {
            "XDG_CURRENT_DESKTOP": xdg,
            "PLASMA_SESSION": plasma,
            "KDE_FULL_SESSION": kde,
            "HYPRLAND_INSTANCE_SIGNATURE": hypr,
            "GNOME_DESKTOP_SESSION_ID": gnome,
        }

    def test_plasma_via_xdg(self):
        assert _detect_desktop(self._env(xdg="KDE")) == "plasma"

    def test_plasma_via_xdg_plasma(self):
        assert _detect_desktop(self._env(xdg="PLASMA")) == "plasma"

    def test_plasma_via_session_env(self):
        assert _detect_desktop(self._env(plasma="1")) == "plasma"

    def test_plasma_via_kde_full(self):
        assert _detect_desktop(self._env(kde="true")) == "plasma"

    def test_hyprland_via_xdg(self):
        assert _detect_desktop(self._env(xdg="Hyprland")) == "hyprland"

    def test_hyprland_via_sig(self):
        assert _detect_desktop(self._env(hypr="abc123")) == "hyprland"

    def test_gnome_via_xdg(self):
        assert _detect_desktop(self._env(xdg="GNOME")) == "gnome"

    def test_xfce(self):
        assert _detect_desktop(self._env(xdg="XFCE")) == "xfce"

    def test_unknown(self):
        assert _detect_desktop(self._env()) == "unknown"


# ---------------------------------------------------------------------------
# Full detect() integration
# ---------------------------------------------------------------------------

class TestDetect:
    def test_returns_detection_result(self, monkeypatch):
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        result = detect()
        assert isinstance(result, DetectionResult)
        assert result.desktop == "plasma"
        assert result.session == "wayland"
        assert result.adapter_class.__name__ == "PlasmaAdapter"

    def test_unknown_de_gives_null_adapter(self, monkeypatch):
        monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
        monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("PLASMA_SESSION", raising=False)
        monkeypatch.delenv("KDE_FULL_SESSION", raising=False)
        monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
        monkeypatch.delenv("GNOME_DESKTOP_SESSION_ID", raising=False)
        result = detect()
        assert result.desktop == "unknown"
        assert result.adapter_class.__name__ == "NullAdapter"

    def test_frozen_dataclass(self, monkeypatch):
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
        monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
        result = detect()
        with pytest.raises(Exception):
            result.desktop = "gnome"  # type: ignore[misc]
