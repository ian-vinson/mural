# tests/test_properties.py
#
# Mural — Animated Wallpaper Platform for Linux
# GPL v3 — see LICENSE

"""Tests for mural/utils/properties.py — property override filtering."""

from mural.utils.properties import real_property_overrides


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
