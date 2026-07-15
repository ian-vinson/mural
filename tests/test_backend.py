# tests/test_backend.py
#
# Mural — Animated Wallpaper Platform for Linux
# GPL v3 — see LICENSE

"""Tests for mural/backend/ — discovery, formats, and runner."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mural.backend.discovery import (
    DiscoveryResult,
    _BINARY_NAME,
    find_assets_path,
    find_lwe_binary,
)
from mural.backend.formats import (
    WallpaperType,
    detect_type,
    find_preview_image,
    is_supported,
)
from mural.backend.runner import _DEBOUNCE_SECONDS, BackendRunner, WallpaperAssignment

# start() is debounced (see runner._DEBOUNCE_SECONDS / issue #35) — tests
# that call start() and immediately assert on the resulting process must
# wait out the window first.
_PAST_DEBOUNCE = _DEBOUNCE_SECONDS + 0.15


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

class TestFindLweBinary:
    def test_env_override_valid(self, tmp_path, monkeypatch):
        binary = tmp_path / "lwe"
        binary.write_text("#!/bin/sh")
        binary.chmod(0o755)
        monkeypatch.setenv("MURAL_LWE_BINARY", str(binary))
        result = find_lwe_binary()
        assert result == binary.resolve()

    def test_env_override_invalid(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setenv("MURAL_LWE_BINARY", str(tmp_path / "missing"))
        result = find_lwe_binary()
        # Falls through to PATH/dir search; likely None in CI
        assert result is None or isinstance(result, Path)

    def test_returns_none_when_not_found(self, monkeypatch):
        monkeypatch.delenv("MURAL_LWE_BINARY", raising=False)
        with patch("shutil.which", return_value=None), \
             patch("pathlib.Path.is_file", return_value=False):
            result = find_lwe_binary()
        assert result is None

    def test_finds_on_path(self, monkeypatch, tmp_path):
        binary = tmp_path / _BINARY_NAME
        binary.write_text("#!/bin/sh")
        binary.chmod(0o755)
        monkeypatch.delenv("MURAL_LWE_BINARY", raising=False)
        with patch("shutil.which", return_value=str(binary)):
            result = find_lwe_binary()
        assert result is not None


class TestFindAssetsPath:
    def test_env_override_valid(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MURAL_ASSETS_PATH", str(tmp_path))
        result = find_assets_path()
        assert result == tmp_path.resolve()

    def test_returns_none_when_not_found(self, monkeypatch):
        monkeypatch.delenv("MURAL_ASSETS_PATH", raising=False)
        with patch("pathlib.Path.is_dir", return_value=False):
            result = find_assets_path()
        assert result is None


# ---------------------------------------------------------------------------
# Formats
# ---------------------------------------------------------------------------

class TestDetectType:
    def test_mp4_is_video(self, tmp_path):
        f = tmp_path / "wallpaper.mp4"
        f.write_bytes(b"")
        assert detect_type(f) == WallpaperType.VIDEO

    def test_webm_is_video(self, tmp_path):
        f = tmp_path / "wallpaper.webm"
        f.write_bytes(b"")
        assert detect_type(f) == WallpaperType.VIDEO

    def test_jpg_is_image(self, tmp_path):
        f = tmp_path / "bg.jpg"
        f.write_bytes(b"")
        assert detect_type(f) == WallpaperType.IMAGE

    def test_scene_directory(self, tmp_path):
        (tmp_path / "project.json").write_text('{"type": "scene"}')
        assert detect_type(tmp_path) == WallpaperType.SCENE

    def test_web_directory(self, tmp_path):
        (tmp_path / "index.html").write_text("<html/>")
        assert detect_type(tmp_path) == WallpaperType.WEB

    def test_unknown_extension(self, tmp_path):
        f = tmp_path / "file.xyz"
        f.write_bytes(b"")
        assert detect_type(f) == WallpaperType.UNKNOWN

    def test_is_supported(self, tmp_path):
        f = tmp_path / "wallpaper.mp4"
        f.write_bytes(b"")
        assert is_supported(f)

    def test_not_supported(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"")
        assert not is_supported(f)


class TestFindPreviewImage:
    def test_image_file_returns_itself(self, tmp_path):
        f = tmp_path / "bg.png"
        f.write_bytes(b"")
        assert find_preview_image(f) == f

    def test_finds_preview_jpg(self, tmp_path):
        preview = tmp_path / "preview.jpg"
        preview.write_bytes(b"")
        assert find_preview_image(tmp_path) == preview

    def test_returns_none_no_preview(self, tmp_path):
        assert find_preview_image(tmp_path) is None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class TestBackendRunner:
    def test_raises_on_empty_assignments(self, tmp_path):
        binary = tmp_path / "lwe"
        binary.write_text("#!/bin/sh\nsleep 100")
        binary.chmod(0o755)
        runner = BackendRunner(binary_path=binary)
        with pytest.raises(ValueError):
            runner.start([])

    def test_raises_on_missing_binary(self, tmp_path):
        runner = BackendRunner(binary_path=tmp_path / "nonexistent_lwe")
        with pytest.raises(FileNotFoundError):
            runner.start([WallpaperAssignment(monitor="DP-3", wallpaper="/some/path")])

    def test_is_running_false_before_start(self, tmp_path):
        runner = BackendRunner(binary_path=tmp_path / "lwe")
        assert not runner.is_running()

    def test_pid_none_before_start(self, tmp_path):
        runner = BackendRunner(binary_path=tmp_path / "lwe")
        assert runner.pid is None

    def test_start_and_stop(self, tmp_path):
        binary = tmp_path / "lwe"
        binary.write_text("#!/bin/sh\nsleep 60")
        binary.chmod(0o755)
        runner = BackendRunner(binary_path=binary, auto_restart=False)
        try:
            runner.start([WallpaperAssignment(monitor="DP-3", wallpaper=str(tmp_path))])
            time.sleep(_PAST_DEBOUNCE)
            assert runner.is_running()
            assert runner.pid is not None
        finally:
            runner.stop()
        assert not runner.is_running()

    def test_context_manager(self, tmp_path):
        binary = tmp_path / "lwe"
        binary.write_text("#!/bin/sh\nsleep 60")
        binary.chmod(0o755)
        with BackendRunner(binary_path=binary, auto_restart=False) as runner:
            runner.start([WallpaperAssignment(monitor="DP-3", wallpaper=str(tmp_path))])
            time.sleep(_PAST_DEBOUNCE)
            assert runner.is_running()
        assert not runner.is_running()


class TestPushLiveProperties:
    def test_false_when_not_running(self, tmp_path):
        runner = BackendRunner(binary_path=tmp_path / "lwe")
        assert runner.push_live_properties(str(tmp_path)) is False

    def test_false_when_wallpaper_not_assigned(self, tmp_path):
        runner = BackendRunner(binary_path=tmp_path / "lwe")
        runner._process = MagicMock(pid=1234)
        runner._process.poll.return_value = None
        runner._assignments = [
            WallpaperAssignment(monitor="DP-3", wallpaper=str(tmp_path / "other")),
        ]
        assert runner.push_live_properties(str(tmp_path)) is False

    def test_writes_per_monitor_payload_and_signals(self, tmp_path, monkeypatch):
        props_file = tmp_path / "live_properties.json"
        monkeypatch.setattr("mural.backend.runner._PROPERTIES_FILE", props_file)
        monkeypatch.setattr(
            "mural.backend.runner.load_overrides",
            lambda path: {"rain": "1", "speed": "2.0"},
        )

        wallpaper = str(tmp_path / "wp")
        runner = BackendRunner(binary_path=tmp_path / "lwe")
        runner._process = MagicMock(pid=4321)
        runner._process.poll.return_value = None
        runner._assignments = [WallpaperAssignment(monitor="DP-3", wallpaper=wallpaper)]

        with patch("os.kill") as mock_kill:
            result = runner.push_live_properties(wallpaper)

        assert result is True
        mock_kill.assert_called_once_with(4321, signal.SIGUSR1)
        payload = json.loads(props_file.read_text())
        assert payload == {"DP-3": {"rain": "1"}}  # "speed" stripped as synthetic

    def test_writes_span_keyed_payload(self, tmp_path, monkeypatch):
        props_file = tmp_path / "live_properties.json"
        monkeypatch.setattr("mural.backend.runner._PROPERTIES_FILE", props_file)
        monkeypatch.setattr(
            "mural.backend.runner.load_overrides",
            lambda path: {"fog": "0.5"},
        )

        wallpaper = str(tmp_path / "wp")
        runner = BackendRunner(binary_path=tmp_path / "lwe", screen_span=True)
        runner._process = MagicMock(pid=999)
        runner._process.poll.return_value = None
        runner._assignments = [
            WallpaperAssignment(monitor="DP-3", wallpaper=wallpaper),
            WallpaperAssignment(monitor="HDMI-1", wallpaper=wallpaper),
        ]

        with patch("os.kill"):
            result = runner.push_live_properties(wallpaper)

        assert result is True
        payload = json.loads(props_file.read_text())
        assert payload == {"span:DP-3": {"fog": "0.5"}}

    def test_properties_file_always_in_build_command(self, tmp_path, monkeypatch):
        props_file = tmp_path / "live_properties.json"
        monkeypatch.setattr("mural.backend.runner._PROPERTIES_FILE", props_file)
        runner = BackendRunner(binary_path=tmp_path / "lwe")
        cmd = runner._build_command(
            [WallpaperAssignment(monitor="DP-3", wallpaper=str(tmp_path))]
        )
        assert "--properties-file" in cmd
        assert cmd[cmd.index("--properties-file") + 1] == str(props_file)
