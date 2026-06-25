# tests/test_platform_client.py
#
# Mural — Animated Wallpaper Platform for Linux
# GPL v3 — see LICENSE

"""Tests for mural/platform/ — models, client, and cache."""

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mural.platform.cache import DownloadCache, _sanitise_name
from mural.platform.models import PlatformPage, PlatformWallpaper
from mural.platform.client import PlatformClient


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TestPlatformWallpaper:
    def _sample(self, **kwargs) -> dict:
        base = {
            "id": str(uuid.uuid4()),
            "title": "Test Wallpaper",
            "description": "A test",
            "author_id": str(uuid.uuid4()),
            "author_name": "tester",
            "type": "video",
            "tags": ["nature", "animated"],
            "resolution": "3440x1440",
            "file_size_bytes": 52_000_000,
            "thumbnail_url": "https://cdn.example.com/thumb.jpg",
            "download_url": "https://cdn.example.com/file.mp4",
            "downloads": 42,
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-02T00:00:00",
        }
        base.update(kwargs)
        return base

    def test_from_dict_roundtrip(self):
        raw = self._sample()
        wp = PlatformWallpaper.from_dict(raw)
        assert wp.id == raw["id"]
        assert wp.title == raw["title"]
        assert wp.tags == raw["tags"]
        assert wp.downloads == 42

    def test_from_dict_defaults_missing_keys(self):
        wp = PlatformWallpaper.from_dict({"id": "x", "title": "Y"})
        assert wp.type == "video"
        assert wp.tags == []
        assert wp.file_size_bytes == 0

    def test_to_dict_roundtrip(self):
        raw = self._sample()
        wp = PlatformWallpaper.from_dict(raw)
        d = wp.to_dict()
        assert d["title"] == raw["title"]
        assert d["tags"] == raw["tags"]

    def test_type_normalised_to_lowercase(self):
        wp = PlatformWallpaper.from_dict({"id": "x", "title": "Y", "type": "VIDEO"})
        assert wp.type == "video"


class TestPlatformPage:
    def test_has_more_true(self):
        page = PlatformPage(items=[], total=100, page=1, limit=24)
        assert page.has_more

    def test_has_more_false_on_last_page(self):
        page = PlatformPage(items=[], total=24, page=1, limit=24)
        assert not page.has_more

    def test_has_more_false_empty(self):
        page = PlatformPage()
        assert not page.has_more


# ---------------------------------------------------------------------------
# Client (mocked HTTP)
# ---------------------------------------------------------------------------

class TestPlatformClient:
    def _mock_response(self, json_data: dict, status: int = 200):
        resp = MagicMock()
        resp.ok = status < 400
        resp.status_code = status
        resp.json.return_value = json_data
        resp.raise_for_status = MagicMock(
            side_effect=None if status < 400 else Exception("HTTP Error")
        )
        return resp

    def test_list_wallpapers_success(self):
        client = PlatformClient()
        payload = {
            "items": [{"id": str(uuid.uuid4()), "title": "WP1", "type": "video"}],
            "total": 1,
        }
        with patch.object(client._session, "get", return_value=self._mock_response(payload)):
            page = client.list_wallpapers()
        assert len(page.items) == 1
        assert page.total == 1

    def test_list_wallpapers_network_failure(self):
        client = PlatformClient()
        with patch.object(client._session, "get", side_effect=Exception("timeout")):
            page = client.list_wallpapers()
        assert page.items == []
        assert page.total == 0

    def test_search_returns_page(self):
        client = PlatformClient()
        payload = {"items": [], "total": 0}
        with patch.object(client._session, "get", return_value=self._mock_response(payload)):
            page = client.search("nature")
        assert isinstance(page, PlatformPage)

    def test_is_reachable_true(self):
        client = PlatformClient()
        with patch.object(client._session, "get", return_value=self._mock_response({"items": []}, 200)):
            assert client.is_reachable()

    def test_is_reachable_false_on_error(self):
        client = PlatformClient()
        with patch.object(client._session, "get", side_effect=Exception("network")):
            assert not client.is_reachable()

    def test_fetch_thumbnail_success(self):
        client = PlatformClient()
        resp = MagicMock()
        resp.ok = True
        resp.content = b"\xff\xd8\xff"  # JPEG magic bytes
        resp.raise_for_status = MagicMock()
        with patch.object(client._session, "get", return_value=resp):
            data = client.fetch_thumbnail("https://example.com/thumb.jpg")
        assert data == b"\xff\xd8\xff"

    def test_fetch_thumbnail_failure_returns_none(self):
        client = PlatformClient()
        with patch.object(client._session, "get", side_effect=Exception("fail")):
            assert client.fetch_thumbnail("https://example.com/x.jpg") is None


# ---------------------------------------------------------------------------
# Download cache
# ---------------------------------------------------------------------------

class TestDownloadCache:
    def test_is_cached_false_initially(self, tmp_path):
        cache = DownloadCache(download_dir=tmp_path, index_file=tmp_path / "index.json")
        assert not cache.is_cached("some-id")

    def test_register_and_is_cached(self, tmp_path):
        cache = DownloadCache(download_dir=tmp_path, index_file=tmp_path / "index.json")
        local = tmp_path / "wallpaper.mp4"
        local.write_bytes(b"fake")
        cache.register("id-1", local)
        assert cache.is_cached("id-1")
        assert cache.get_local_path("id-1") == local

    def test_stale_entry_removed(self, tmp_path):
        cache = DownloadCache(download_dir=tmp_path, index_file=tmp_path / "index.json")
        cache._index["id-x"] = str(tmp_path / "missing.mp4")
        result = cache.all_cached()
        assert "id-x" not in result

    def test_remove_deletes_file(self, tmp_path):
        cache = DownloadCache(download_dir=tmp_path, index_file=tmp_path / "index.json")
        local = tmp_path / "wallpaper.mp4"
        local.write_bytes(b"data")
        cache.register("id-2", local)
        cache.remove("id-2", delete_file=True)
        assert not local.exists()
        assert not cache.is_cached("id-2")

    def test_clear(self, tmp_path):
        cache = DownloadCache(download_dir=tmp_path, index_file=tmp_path / "index.json")
        f1 = tmp_path / "a.mp4"
        f1.write_bytes(b"a")
        f2 = tmp_path / "b.mp4"
        f2.write_bytes(b"b")
        cache.register("id-a", f1)
        cache.register("id-b", f2)
        count = cache.clear()
        assert count == 2
        assert not f1.exists()

    def test_sanitise_name(self):
        assert _sanitise_name("My Cool Wallpaper!") == "My Cool Wallpaper_"
        assert _sanitise_name("") == ""
