"""Unit tests for src.foundation.config_setup.Config accessors.

Covers default values and round-trips of typed settings, fallback on
corrupt values, atomic save, and the legacy theme_file migration.
"""

import configparser
import logging

import pytest

from src.foundation import config_setup


@pytest.fixture
def fresh_config(tmp_path, monkeypatch):
    """A Config bound to a scratch config.ini, with the singleton reset so
    each test gets its own instance and file."""
    scratch_ini = tmp_path / "config.ini"
    monkeypatch.setattr(config_setup, "config", lambda name: str(scratch_ini))
    config_setup.Config._instance = None
    config_setup.Config._initialized = False
    cfg = config_setup.Config()
    yield cfg, scratch_ini
    config_setup.Config._instance = None
    config_setup.Config._initialized = False


def _reload(scratch_ini):
    config_setup.Config._instance = None
    config_setup.Config._initialized = False
    return config_setup.Config()


@pytest.mark.parametrize(
    ("name", "default", "value", "section", "key", "raw_value"),
    [
        ("excluded_roles", [], ["Composer", "Remixer"], "library", "excluded_roles", "Composer,Remixer"),
        ("transcode_cache_max_mb", 2048, 512, "sync", "transcode_cache_max_mb", "512"),
        ("waveform_cache_max_mb", 256, 512, "player", "waveform_cache_max_mb", "512"),
        ("waveform_display_mode", "linear", "log", "player", "display_mode", "log"),
        ("nav_item_order", [], ["Tracks", "Albums", "Artists"], "navigation", "item_order", "Tracks,Albums,Artists"),
        ("nav_hidden_items", [], ["Genres", "Places"], "navigation", "hidden_items", "Genres,Places"),
        ("menu_bar_auto_hide", False, True, None, None, None),
    ],
)
def test_setting_defaults_and_round_trips_through_config_file(fresh_config, name, default, value, section, key, raw_value):
    cfg, scratch_ini = fresh_config
    assert getattr(cfg, f"get_{name}")() == default

    getattr(cfg, f"set_{name}")(value)
    cfg.save()

    assert getattr(_reload(scratch_ini), f"get_{name}")() == value
    if section:
        raw = configparser.ConfigParser()
        raw.read(scratch_ini)
        assert raw[section][key] == raw_value


# --- finalize audit: robustness fixes -------------------------------------


def _write_raw(scratch_ini, section, key, value):
    raw = configparser.RawConfigParser()
    raw.read(scratch_ini)
    if not raw.has_section(section):
        raw.add_section(section)
    raw.set(section, key, value)
    with scratch_ini.open("w") as f:
        raw.write(f)


def test_percent_sign_values_round_trip(fresh_config):
    cfg, scratch_ini = fresh_config
    cfg.set_equalizer_custom_preset_name("100% Bass")
    cfg.set_album_view_filters({"query": "50%"})
    cfg.save()

    reloaded = _reload(scratch_ini)
    assert reloaded.get_equalizer_custom_preset_name() == "100% Bass"
    assert reloaded.get_album_view_filters() == {"query": "50%"}


def test_themes_dir_is_absolute_project_themes_dir(fresh_config):
    from src.foundation.asset_paths import THEMES_DIR

    cfg, _ = fresh_config
    assert cfg.themes_dir == THEMES_DIR
    assert cfg.themes_dir.is_absolute()


@pytest.mark.parametrize(
    ("section", "key", "getter", "expected"),
    [
        ("playback", "volume", "get_volume", 75),
        ("playback", "shuffle", "get_shuffle", False),
        ("display", "ui_scale", "get_ui_scale", 1.0),
        ("nowplaying", "lyrics_sync_offset", "get_lyrics_sync_offset", -5),
    ],
)
def test_corrupt_typed_value_falls_back_to_default(fresh_config, section, key, getter, expected):
    _, scratch_ini = fresh_config
    _write_raw(scratch_ini, section, key, "garbage")
    assert getattr(_reload(scratch_ini), getter)() == expected


def test_int_list_skips_blank_and_invalid_items(fresh_config):
    _, scratch_ini = fresh_config
    _write_raw(scratch_ini, "track_view", "column_widths", "100,,80, x,120")
    assert _reload(scratch_ini).get_track_view_column_widths() == [100, 80, 120]


@pytest.mark.parametrize(("stored", "expected"), [("debug", logging.DEBUG), ("Warning", logging.WARNING), ("basicConfig", logging.INFO), ("nonsense", logging.INFO)])
def test_logging_level_is_case_insensitive_and_always_an_int(fresh_config, stored, expected):
    _, scratch_ini = fresh_config
    _write_raw(scratch_ini, "logging", "level", stored)
    assert _reload(scratch_ini).get_logging_level() == expected


@pytest.mark.parametrize(("stored", "expected"), [("1.0,2.0", [1.0, 2.0] + [0.0] * 8), (",".join(["3.0"] * 12), [3.0] * 10), ("nan,inf,x,-2.5", [0.0, 0.0, 0.0, -2.5] + [0.0] * 6)])
def test_band_gains_always_ten_finite_values(fresh_config, stored, expected):
    _, scratch_ini = fresh_config
    _write_raw(scratch_ini, "equalizer", "band_gains", stored)
    assert _reload(scratch_ini).get_equalizer_band_gains() == expected


def test_save_is_atomic_and_leaves_no_temp_file(fresh_config):
    cfg, scratch_ini = fresh_config
    cfg.set_volume(42)
    cfg.save()
    assert not scratch_ini.with_name(scratch_ini.name + ".tmp").exists()
    assert _reload(scratch_ini).get_volume() == 42


def test_failed_save_keeps_previous_file(fresh_config, monkeypatch):
    cfg, scratch_ini = fresh_config
    before = scratch_ini.read_text()

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(cfg.config, "write", boom)
    cfg.set_volume(1)
    cfg.save()  # logged, not raised
    assert scratch_ini.read_text() == before
    assert not scratch_ini.with_name(scratch_ini.name + ".tmp").exists()


# ---- legacy app.theme_file migration (bugs.md #596) ---------------------------


def _write_ini(scratch_ini, text):
    scratch_ini.write_text(text, encoding="utf-8")
    return _reload(scratch_ini)


@pytest.mark.parametrize(
    ("ini", "expected"),
    [
        ("[app]\ntheme_file = light_mode.qss\n[display]\ntheme =\n", "light_mode"),
        ("[app]\ntheme_file = light_mode.qss\n", "light_mode"),
        ("[app]\ntheme_file = light_mode.qss\n[display]\ntheme = dark_mode\n", "dark_mode"),
    ],
    ids=["fills-empty-theme", "creates-display-section", "display-theme-wins"],
)
def test_legacy_theme_file_is_migrated_and_removed(fresh_config, ini, expected):
    _, scratch_ini = fresh_config
    cfg = _write_ini(scratch_ini, ini)

    assert cfg.get_display_theme() == expected
    assert not cfg.config.has_option("app", "theme_file")
    raw = configparser.ConfigParser()
    raw.read(scratch_ini)
    assert not raw.has_option("app", "theme_file")
    assert raw["display"]["theme"] == expected


def test_theme_path_and_available_themes_use_stems(fresh_config):
    cfg, _ = fresh_config
    cfg.set_display_theme("dark_mode")

    assert cfg.get_theme_path() == cfg.themes_dir / "dark_mode.qss"
    assert all(not name.endswith(".qss") for name in cfg.get_available_themes())
