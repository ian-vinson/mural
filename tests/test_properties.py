# tests/test_properties.py
#
# Mural — Animated Wallpaper Platform for Linux
# GPL v3 — see LICENSE

"""Tests for mural/utils/properties.py — property override filtering."""

import json

from mural.utils.properties import parse_properties, real_property_overrides


def _write_project(tmp_path, properties):
    proj = tmp_path / "project.json"
    proj.write_text(json.dumps({
        "type": "scene",
        "general": {"properties": properties},
    }))
    return str(proj)


class TestRealPropertyOverrides:
    def test_strips_synthetic_keys(self):
        overrides = {
            "speed": "2.0",
            "loop_mode": "no_loop",
            "scaling": "fill",
            "rain": "1",
            "fogamount": "0.5",
        }
        assert real_property_overrides(overrides) == {
            "rain": "1",
            "fogamount": "0.5",
        }

    def test_empty_input(self):
        assert real_property_overrides({}) == {}

    def test_no_synthetic_keys_passes_through_unchanged(self):
        overrides = {"rain": "1", "bloom": "0.75"}
        assert real_property_overrides(overrides) == overrides

    def test_only_synthetic_keys_yields_empty(self):
        overrides = {"speed": "1.0", "loop_mode": "default", "scaling": "default"}
        assert real_property_overrides(overrides) == {}


class TestParsePropertiesTypeStrings:
    """WE's real on-disk type strings, confirmed against a live library
    sweep (1,172 wallpapers) — not the assumed-but-wrong ones."""

    def test_combo_type_string_is_recognized(self, tmp_path):
        path = _write_project(tmp_path, {
            "barstyle": {
                "type": "combo",
                "text": "Bar Style",
                "value": "1",
                "options": [{"label": "Up Down", "value": "1"},
                            {"label": "Top", "value": "2"}],
            },
        })
        props = parse_properties(path)
        assert len(props) == 1
        assert props[0].key == "barstyle"
        assert props[0].type == "combo"
        assert props[0].options == ["Up Down", "Top"]

    def test_text_type_string_is_recognized(self, tmp_path):
        path = _write_project(tmp_path, {
            "b": {"type": "text", "value": "hello"},
        })
        props = parse_properties(path)
        assert len(props) == 1
        assert props[0].type == "text"
        assert props[0].value == "hello"

    def test_textinput_alias_still_recognized(self, tmp_path):
        path = _write_project(tmp_path, {
            "b": {"type": "textinput", "value": "hello"},
        })
        props = parse_properties(path)
        assert len(props) == 1
        assert props[0].type == "text"

    def test_scenetexture_maps_to_texture_type(self, tmp_path):
        path = _write_project(tmp_path, {
            "b1": {"type": "scenetexture", "text": "Background", "value": ""},
        })
        props = parse_properties(path)
        assert len(props) == 1
        assert props[0].type == "texture"

    def test_group_and_usershortcut_still_skipped(self, tmp_path):
        path = _write_project(tmp_path, {
            "section": {"type": "group", "text": "Section"},
            "shortcut": {"type": "usershortcut", "text": "Open folder"},
            "rain": {"type": "bool", "value": "1"},
        })
        props = parse_properties(path)
        assert [p.key for p in props] == ["rain"]

    def test_unknown_junk_type_is_dropped(self, tmp_path):
        path = _write_project(tmp_path, {
            "weird": {"type": "uwu", "value": "1"},
            "rain": {"type": "bool", "value": "1"},
        })
        props = parse_properties(path)
        assert [p.key for p in props] == ["rain"]


class TestParsePropertiesMalformedNumeric:
    """A malformed min/max/precision on one property must drop only that
    property, not raise and blank the whole file (properties.py:132-140)."""

    def test_malformed_min_skips_only_that_property(self, tmp_path):
        path = _write_project(tmp_path, {
            "badslider": {"type": "slider", "value": "0.5", "min": "not-a-number"},
            "goodslider": {"type": "slider", "value": "0.5", "min": 0.0, "max": 1.0},
        })
        props = parse_properties(path)
        assert [p.key for p in props] == ["goodslider"]

    def test_malformed_precision_skips_only_that_property(self, tmp_path):
        path = _write_project(tmp_path, {
            "badslider": {"type": "slider", "value": "0.5", "precision": "auto"},
            "rain": {"type": "bool", "value": "1"},
        })
        props = parse_properties(path)
        assert [p.key for p in props] == ["rain"]

    def test_no_exception_raised(self, tmp_path):
        path = _write_project(tmp_path, {
            "badslider": {"type": "slider", "value": "0.5", "min": [1, 2]},
        })
        # Must not raise — a single malformed property is skipped, not fatal.
        assert parse_properties(path) == []
