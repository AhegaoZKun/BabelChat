"""Tests for overlay theming and config forward/backward compatibility."""

import json

from app.config import AppConfig
from app.overlay_theme import (
    PRESET_ORDER,
    PRESETS,
    SLOT_ORDER,
    hex_to_rgb,
    resolve_theme,
)


def test_presets_cover_all_slots():
    for name, theme in PRESETS.items():
        assert set(theme.channel_colors) == set(SLOT_ORDER), name


def test_preset_order_matches_presets():
    assert set(PRESET_ORDER) == set(PRESETS) | {"custom"}


def test_resolve_named_preset():
    cfg = AppConfig(overlay_theme="midnight")
    assert resolve_theme(cfg) == PRESETS["midnight"]


def test_resolve_unknown_falls_back_to_custom_over_default():
    cfg = AppConfig(overlay_theme="nonexistent", overlay_bg_color="#123456")
    theme = resolve_theme(cfg)
    assert theme.bg_color == "#123456"
    # untouched fields come from the default preset
    assert theme.translation_color == PRESETS["wow"].translation_color


def test_resolve_custom_channel_overrides_partial():
    cfg = AppConfig(
        overlay_theme="custom",
        overlay_channel_colors={"guild": "#010203", "bogus_slot": "#000000", "say": "not-a-color"},
    )
    theme = resolve_theme(cfg)
    assert theme.channel_colors["guild"] == "#010203"
    assert "bogus_slot" not in theme.channel_colors
    # invalid hex is ignored → default kept
    assert theme.channel_colors["say"] == PRESETS["wow"].channel_colors["say"]


def test_resolve_invalid_colors_ignored():
    cfg = AppConfig(overlay_theme="custom", overlay_bg_color="red", overlay_corner_radius=999)
    theme = resolve_theme(cfg)
    assert theme.bg_color == PRESETS["wow"].bg_color
    assert theme.corner_radius <= 32


def test_hex_to_rgb():
    assert hex_to_rgb("#FF8000") == (255, 128, 0)
    assert hex_to_rgb("junk") == (0, 0, 0)


def test_config_load_ignores_unknown_keys(tmp_path):
    """Old config files with removed fields must not crash startup."""
    p = tmp_path / "config.json"
    p.write_text(
        json.dumps({"google_api_key": "leftover", "argos_enabled": True, "overlay_theme": "light"}),
        encoding="utf-8",
    )
    cfg = AppConfig.load(str(p))
    assert cfg.overlay_theme == "light"
    assert not hasattr(cfg, "google_api_key")


def test_config_roundtrip_with_theme(tmp_path):
    p = tmp_path / "config.json"
    cfg = AppConfig(overlay_theme="custom", overlay_channel_colors={"raid": "#112233"})
    cfg.save(str(p))
    loaded = AppConfig.load(str(p))
    assert loaded.overlay_channel_colors == {"raid": "#112233"}
    assert resolve_theme(loaded).channel_colors["raid"] == "#112233"
