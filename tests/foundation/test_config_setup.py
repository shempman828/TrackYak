"""Unit tests for src.foundation.config_setup.Config accessors.

Covers the CSV-list config fields (excluded_genres / excluded_roles) that
back the "Skipped …" parse-ignore lists — see
docs/specs/role_parse_ignore_list.md.
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


def test_excluded_roles_round_trips_through_config_file(fresh_config):
    cfg, scratch_ini = fresh_config

    cfg.set_excluded_roles(["Composer", "Remixer"])
    cfg.save()

    reloaded = _reload(scratch_ini)
    assert reloaded.get_excluded_roles() == ["Composer", "Remixer"]

    raw = configparser.ConfigParser()
    raw.read(scratch_ini)
    assert raw["library"]["excluded_roles"] == "Composer,Remixer"


def test_excluded_roles_defaults_to_empty_list_when_unset(fresh_config):
    cfg, _ = fresh_config
    assert cfg.get_excluded_roles() == []


def test_excluded_roles_and_genres_are_independent(fresh_config):
    cfg, scratch_ini = fresh_config

    cfg.set_excluded_genres(["Noise"])
    cfg.set_excluded_roles(["Engineer"])
    cfg.save()

    reloaded = _reload(scratch_ini)
    assert reloaded.get_excluded_genres() == ["Noise"]
    assert reloaded.get_excluded_roles() == ["Engineer"]


# --- transcode cache size cap (idea 57) ------------------------------------ AC9


def test_transcode_cache_max_mb_defaults_to_2048(fresh_config):
    cfg, _ = fresh_config
    assert cfg.get_transcode_cache_max_mb() == 2048


def test_transcode_cache_max_mb_round_trips_through_config_file(fresh_config):
    cfg, scratch_ini = fresh_config

    cfg.set_transcode_cache_max_mb(512)
    cfg.save()

    reloaded = _reload(scratch_ini)
    assert reloaded.get_transcode_cache_max_mb() == 512

    raw = configparser.ConfigParser()
    raw.read(scratch_ini)
    assert raw["sync"]["transcode_cache_max_mb"] == "512"


# --- waveform peak cache size cap (idea 58) ------------------------------- AC12


def test_waveform_cache_max_mb_defaults_to_256(fresh_config):
    cfg, _ = fresh_config
    assert cfg.get_waveform_cache_max_mb() == 256


def test_waveform_cache_max_mb_round_trips_through_config_file(fresh_config):
    cfg, scratch_ini = fresh_config

    cfg.set_waveform_cache_max_mb(512)
    cfg.save()

    reloaded = _reload(scratch_ini)
    assert reloaded.get_waveform_cache_max_mb() == 512

    raw = configparser.ConfigParser()
    raw.read(scratch_ini)
    assert raw["player"]["waveform_cache_max_mb"] == "512"


# --- waveform display mode (linear vs. perceptual/log) -------------------- AC1


def test_waveform_display_mode_defaults_to_linear(fresh_config):
    cfg, _ = fresh_config
    assert cfg.get_waveform_display_mode() == "linear"


def test_waveform_display_mode_round_trips_through_config_file(fresh_config):
    cfg, scratch_ini = fresh_config

    cfg.set_waveform_display_mode("log")
    cfg.save()

    reloaded = _reload(scratch_ini)
    assert reloaded.get_waveform_display_mode() == "log"

    raw = configparser.ConfigParser()
    raw.read(scratch_ini)
    assert raw["player"]["display_mode"] == "log"


# --- navbar order and visibility ------------------------------------------ AC1, AC2


def test_nav_item_order_defaults_to_empty_list_when_unset(fresh_config):
    cfg, _ = fresh_config
    assert cfg.get_nav_item_order() == []


def test_nav_item_order_round_trips_through_config_file(fresh_config):
    cfg, scratch_ini = fresh_config

    cfg.set_nav_item_order(["Tracks", "Albums", "Artists"])
    cfg.save()

    reloaded = _reload(scratch_ini)
    assert reloaded.get_nav_item_order() == ["Tracks", "Albums", "Artists"]

    raw = configparser.ConfigParser()
    raw.read(scratch_ini)
    assert raw["navigation"]["item_order"] == "Tracks,Albums,Artists"


def test_nav_hidden_items_defaults_to_empty_list_when_unset(fresh_config):
    cfg, _ = fresh_config
    assert cfg.get_nav_hidden_items() == []


def test_nav_hidden_items_round_trips_through_config_file(fresh_config):
    cfg, scratch_ini = fresh_config

    cfg.set_nav_hidden_items(["Genres", "Places"])
    cfg.save()

    reloaded = _reload(scratch_ini)
    assert reloaded.get_nav_hidden_items() == ["Genres", "Places"]

    raw = configparser.ConfigParser()
    raw.read(scratch_ini)
    assert raw["navigation"]["hidden_items"] == "Genres,Places"


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


def test_menu_bar_auto_hide_round_trips(fresh_config):
    cfg, scratch_ini = fresh_config
    assert cfg.get_menu_bar_auto_hide() is False
    cfg.set_menu_bar_auto_hide(True)
    cfg.save()
    assert _reload(scratch_ini).get_menu_bar_auto_hide() is True
