"""Persistent INI-backed application configuration."""

from collections.abc import Callable
import configparser
from dataclasses import dataclass
import json
import logging
import math
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QPoint, QSize

from src.foundation.asset_paths import THEMES_DIR, config
from src.foundation.logger_config import logger


@dataclass(frozen=True)
class ConfigField:
    """Declarative description of one config-backed get_*/set_* accessor pair."""

    name: str
    section: str
    keys: tuple
    decode: Callable[["Config", str, tuple], Any]
    # encode(self, section, keys, *values): *values mirrors the set_* args (two for legend size/position).
    encode: Callable[..., None]
    getter_prefix: str = "get"


_EQ_BAND_COUNT = 10

_PRIMITIVE_ACCESSORS = {
    "str": ("_get_str", "_set_str"),
    "bool": ("_get_bool", "_set_bool"),
    "int": ("_get_int", "_set_int"),
    "float": ("_get_float", "_set_float"),
    "list": ("_get_list", "_set_list"),
    "int_list": ("_get_int_list", "_set_list"),
}


def _primitive(name, section, key, kind, default, prefix="get") -> ConfigField:
    """Build a ConfigField for a single-key value of a built-in type."""
    getter_name, setter_name = _PRIMITIVE_ACCESSORS[kind]

    def decode(self, section, keys):
        """Read the value through the typed getter for this kind."""
        return getattr(self, getter_name)(section, keys[0], fallback=default)

    def encode(self, section, keys, *values):
        """Write the value through the typed setter for this kind."""
        getattr(self, setter_name)(section, keys[0], values[0])

    return ConfigField(name, section, (key,), decode, encode, prefix)


# --- Custom codecs for fields that aren't a single primitive value ---------


def _decode_base_directory(self, section, keys):
    """Return the library root as a Path (default ~/Music)."""
    return Path(self._get_str(section, keys[0], fallback=str(Path.home() / "Music")))


def _encode_base_directory(self, section, keys, *values):
    """Store the library root path as a string."""
    self._set_str(section, keys[0], str(values[0]))


def _decode_csv_list(self, section, keys):
    """Return a comma-joined string as a list of trimmed, non-empty names."""
    raw = self._get_str(section, keys[0], fallback="")
    return [g.strip() for g in raw.split(",") if g.strip()]


def _encode_csv_list(self, section, keys, *values):
    """Store a list of names as a trimmed comma-joined string."""
    self._set_str(section, keys[0], ",".join(g.strip() for g in values[0] if g.strip()))


def _decode_window_size(self, section, keys):
    """Return the stored window size as a QSize (default 1280x720)."""
    try:
        size_str = self.config.get(section, keys[0], fallback="1280,720")
        width, height = map(int, size_str.split(","))
        return QSize(width, height)
    except ValueError:
        return QSize(1280, 720)


def _encode_window_size(self, section, keys, *values):
    """Store a QSize as 'width,height'."""
    value = values[0]
    self._set_str(section, keys[0], f"{value.width()},{value.height()}")


def _decode_window_position(self, section, keys):
    """Return the stored window position as a QPoint (default 100,100)."""
    try:
        pos_str = self.config.get(section, keys[0], fallback="100,100")
        x, y = map(int, pos_str.split(","))
        return QPoint(x, y)
    except ValueError:
        return QPoint(100, 100)


def _encode_window_position(self, section, keys, *values):
    """Store a QPoint as 'x,y'."""
    value = values[0]
    self._set_str(section, keys[0], f"{value.x()},{value.y()}")


def _decode_window_state(self, section, keys):
    """Return the stored base64 window state as a QByteArray."""
    state_b64 = self.config.get(section, keys[0], fallback="")
    if state_b64:
        try:
            return QByteArray.fromBase64(state_b64.encode())
        except ValueError:
            return QByteArray()
    return QByteArray()


def _encode_window_state(self, section, keys, *values):
    """Store a QByteArray window state as base64 text."""
    self._set_str(section, keys[0], values[0].toBase64().data().decode())


def _decode_logging_level(self, section, keys):
    """Return the stored logging level as a logging int constant (default INFO)."""
    level_name = self._get_str(section, keys[0], fallback="INFO").strip().upper()
    # getattr(logging, name) is unsafe here: "debug" resolves to the logging.debug function.
    return logging.getLevelNamesMapping().get(level_name, logging.INFO)


def _encode_logging_level(self, section, keys, *values):
    """Store a logging level name; ignore names that are not valid levels."""
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    value = values[0]
    if value.upper() in valid_levels:
        self._set_str(section, keys[0], value.upper())


def _decode_band_gains(self, section, keys):
    """Return exactly ten finite EQ band gains in dB."""
    gains_str = self._get_str(section, keys[0], fallback="")
    gains = []
    for item in gains_str.split(","):
        try:
            gain = float(item)
        except ValueError:
            gain = 0.0
        gains.append(gain if math.isfinite(gain) else 0.0)
    # Pad or truncate so the equalizer always gets one gain per band.
    return (gains + [0.0] * _EQ_BAND_COUNT)[:_EQ_BAND_COUNT]


def _encode_band_gains(self, section, keys, *values):
    """Store EQ band gains as comma-separated one-decimal values."""
    self._set_str(section, keys[0], ",".join(f"{gain:.1f}" for gain in values[0]))


def _decode_lyrics_offset(self, section, keys):
    """Return the lyrics sync offset in tenths of a second (default -5)."""
    return self._get_int(section, keys[0], fallback=-5)


def _encode_lyrics_offset(self, section, keys, *values):
    """Store the lyrics sync offset in tenths of a second."""
    self._set_int(section, keys[0], int(values[0]))


def _decode_legend_size(self, section, keys):
    """Return the influence legend size as (width, height)."""
    width_key, height_key = keys
    width = self._get_int(section, width_key, fallback=240)
    height = self._get_int(section, height_key, fallback=260)
    return width, height


def _encode_legend_size(self, section, keys, *values):
    """Store the influence legend size as two int keys."""
    width_key, height_key = keys
    width, height = values
    self._set_int(section, width_key, int(width))
    self._set_int(section, height_key, int(height))


def _decode_legend_position(self, section, keys):
    """Return the influence legend position as (x, y), or None if unset."""
    x_key, y_key = keys
    if not self.config.has_option(section, x_key) or not self.config.has_option(section, y_key):
        return None
    return self._get_int(section, x_key, fallback=0), self._get_int(section, y_key, fallback=0)


def _encode_legend_position(self, section, keys, *values):
    """Store the influence legend position as two int keys."""
    x_key, y_key = keys
    x, y = values
    self._set_int(section, x_key, int(x))
    self._set_int(section, y_key, int(y))


def _decode_json_dict(self, section, keys):
    """Return a JSON-encoded dict, or {} if unset or invalid."""
    raw = self._get_str(section, keys[0], fallback="")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _encode_json_dict(self, section, keys, *values):
    """Store a dict as JSON text."""
    self._set_str(section, keys[0], json.dumps(values[0]))


class Config:
    """Singleton wrapper around config.ini with generated typed accessors."""

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self.config_path = Path(config("config.ini"))
            # No interpolation: values such as paths, preset names and JSON can contain "%".
            self.config = configparser.ConfigParser(interpolation=None)
            self.themes_dir = THEMES_DIR
            self._ensure_themes_dir()
            self._ensure_config_dir()  # Create config directory if needed
            self.load()
            self._initialized = True

    def _ensure_config_dir(self):
        """Create the config directory if it does not exist."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def _ensure_themes_dir(self):
        """Create the themes directory if it does not exist."""
        self.themes_dir.mkdir(parents=True, exist_ok=True)

    def load(self):
        """Load configuration from file, or create and save the defaults."""
        if self.config_path.exists():
            try:
                self.config.read(self.config_path, encoding="utf-8")
                logger.info(f"Configuration loaded from {self.config_path}")
                self._migrate_legacy_queue_keys()
            except (configparser.Error, OSError) as e:
                logger.error(f"Error loading config: {e}")
                self._create_default_config()
        else:
            self._create_default_config()
            self.save()

    def _migrate_legacy_queue_keys(self):
        """Remove legacy queue/history ID keys from config.ini."""
        # These IDs now live in queue_state.json; the old keys could hold tens of thousands of entries.
        if not self.config.has_section("queue"):
            return
        removed = False
        for legacy_key in ("history_ids", "queue_ids"):
            if self.config.has_option("queue", legacy_key):
                self.config.remove_option("queue", legacy_key)
                removed = True
        if removed:
            logger.info("Migrated legacy queue/history IDs out of config.ini")
            self.save()

    def _create_default_config(self):
        """Fill self.config with the default sections and values."""
        # Window section
        self.config["window"] = {"size": "1280,720", "position": "100,100", "state": "", "maximized": "false"}
        # Display section (NEW)
        self.config["display"] = {"theme": "dark_mode", "ui_scale": "1.0", "font_family": "Inter", "font_size": "10", "blur_explicit_art": "false", "censor_explicit_words": "false"}
        # App section
        self.config["app"] = {
            "music_dir": str(Path.home() / "Music"),
            "first_run": "true",
            # theme_file (app) and display.theme both name the active theme; see bugs.md #596.
        }

        # Library section
        self.config["library"] = {"root_directory": str(Path.home() / "Music"), "scan_on_startup": "true", "auto_refresh": "false", "excluded_genres": "", "excluded_roles": ""}

        # Playback section
        self.config["playback"] = {
            "volume": "75",
            "shuffle": "false",
            "repeat": "none",  # none, one, all
        }

        # Audio section
        self.config["audio"] = {"output_device": "default", "buffer_size": "1024", "exclusive_mode": "false"}

        # Logging section
        self.config["logging"] = {
            "level": "INFO",  # DEBUG, INFO, WARNING, ERROR, CRITICAL
            "console_enabled": "true",
            "file_enabled": "true",
            "max_file_size_mb": "10",
            "backup_count": "14",
        }
        # Equalizer section
        self.config["equalizer"] = {
            "enabled": "false",
            "custom_preset_name": "My Custom EQ",
            # Band gains stored as comma-separated values
            "band_gains": "0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0",
            "presets": ("Flat,Bass Boost,Treble Boost,Rock,Pop,Jazz,Classical,Electronic,Hip Hop,Acoustic,Vocal Boost,Dance"),
        }
        # Queue section
        # Note: queue/history track IDs live in queue_state.json, not here —
        # a shuffled full-library queue can be tens of thousands of IDs, which
        # doesn't belong in a human-editable settings file.
        self.config["queue"] = {"persist_queue": "true"}
        # Sync section — device sync options that aren't per-profile. The
        # transcode cache dir (cache/transcode/) is shared across every profile,
        # so its size cap lives here. 0 disables eviction (unlimited).
        self.config["sync"] = {"transcode_cache_max_mb": "2048"}
        # Player section — playback UI options that aren't per-device. The
        # waveform seek bar's per-track peak cache (cache/waveforms/) is
        # capped here; 0 disables eviction (unlimited).
        self.config["player"] = {"waveform_cache_max_mb": "256"}
        self.config["track_view"] = {
            "visible_columns": "track_file_name,artist_name,album_name,title,genre,duration,year",
            "column_order": "track_file_name,artist_name,album_name,title,genre,duration,year",
            "column_widths": "",
        }
        self.config["album_view"] = {"filters": ""}
        self.config["artist_view"] = {"filters": ""}
        self.config["nowplaying"] = {
            "lyrics_sync_offset": "-5",  # stored as tenths of a second (int)
            "manual_sync_reaction_ms": "200",
        }
        self.config["influences"] = {"legend_visible": "true", "cluster_names": ""}

    def save(self):
        """Write configuration to file atomically."""
        # Write to a temp file and swap it in, so a crash mid-write cannot corrupt config.ini.
        tmp_path = self.config_path.with_name(self.config_path.name + ".tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as configfile:
                self.config.write(configfile)
            tmp_path.replace(self.config_path)
            logger.info(f"Configuration saved to {self.config_path}")
        except OSError as e:
            logger.error(f"Error saving config: {e}")
            tmp_path.unlink(missing_ok=True)

    # --- Generic section/type helpers -------------------------------------
    # Every field in _CONFIG_FIELDS below is a thin wrapper around one of
    # these. Setters ensure the section exists first, since not all sections
    # are guaranteed to be present in configs written by older app versions.

    def _ensure_section(self, section: str):
        """Create `section` if it is missing."""
        if section not in self.config:
            self.config[section] = {}

    def _get_str(self, section: str, key: str, fallback: str = "") -> str:
        """Return a string value, or `fallback` if unset."""
        return self.config.get(section, key, fallback=fallback)

    def _set_str(self, section: str, key: str, value: str):
        """Store a string value, creating the section if needed."""
        self._ensure_section(section)
        self.config.set(section, key, value)

    def _get_bool(self, section: str, key: str, fallback: bool = False) -> bool:
        """Return a bool value, or `fallback` if unset or invalid."""
        return self._get_typed(self.config.getboolean, section, key, fallback)

    def _set_bool(self, section: str, key: str, value: bool):
        """Store a bool as 'true'/'false'."""
        self._set_str(section, key, str(value).lower())

    def _get_int(self, section: str, key: str, fallback: int = 0) -> int:
        """Return an int value, or `fallback` if unset or invalid."""
        return self._get_typed(self.config.getint, section, key, fallback)

    def _set_int(self, section: str, key: str, value: int):
        """Store an int value."""
        self._set_str(section, key, str(value))

    def _get_float(self, section: str, key: str, fallback: float = 0.0) -> float:
        """Return a float value, or `fallback` if unset or invalid."""
        return self._get_typed(self.config.getfloat, section, key, fallback)

    def _get_typed(self, read: Callable, section: str, key: str, fallback):
        """Read a value with a typed configparser getter, falling back on a corrupt value."""
        try:
            return read(section, key, fallback=fallback)
        except ValueError:
            logger.warning(f"Invalid config value for [{section}] {key}; using default {fallback!r}")
            return fallback

    def _set_float(self, section: str, key: str, value: float):
        """Store a float value."""
        self._set_str(section, key, str(value))

    def _get_list(self, section: str, key: str, fallback: str = "") -> list:
        """Return a comma-separated value as a list of strings."""
        raw = self.config.get(section, key, fallback=fallback)
        return raw.split(",") if raw else []

    def _set_list(self, section: str, key: str, items: list):
        """Store a list as a comma-separated string."""
        self._set_str(section, key, ",".join(str(item) for item in items))

    def _get_int_list(self, section: str, key: str, fallback: str = "") -> list:
        """Return a comma-separated value as a list of ints, skipping blank or invalid items."""
        raw = self.config.get(section, key, fallback=fallback)
        values = []
        for item in raw.split(","):
            try:
                values.append(int(item))
            except ValueError:
                continue
        return values

    # Theme management (filesystem-backed, not config-value accessors)
    def get_available_themes(self):
        """Return the file names of the available .qss themes."""
        theme_files = []
        if self.themes_dir.exists():
            for file in self.themes_dir.glob("*.qss"):
                theme_files.append(file.name)
        return theme_files

    def get_theme_path(self, theme_file=None):
        """Return the full path to a theme file (default: the configured one)."""
        if theme_file is None:
            theme_file = self.get_theme_file()
        return self.themes_dir / theme_file

    def save_equalizer_settings(self, enabled: bool, band_gains: list, preset_name: str = "Custom"):
        """Store all EQ settings and save the file."""
        self.set_equalizer_enabled(enabled)
        self.set_equalizer_band_gains(band_gains)
        if preset_name != "Custom":
            self.set_equalizer_custom_preset_name(preset_name)
        self.save()


# --- Declarative table of config-backed accessors ---------------------------
# Each entry generates a get_{name}/set_{name} method pair (or is_{name} for
# boolean flags phrased as questions) on Config. Simple fields reuse the
# generic codecs from _primitive(); fields with non-trivial storage (multi-key,
# JSON, Qt types, validation) supply their own decode/encode pair defined
# above. Either way, every accessor is generated through the same mechanism.

_CONFIG_FIELDS = [
    _primitive("theme_file", "app", "theme_file", "str", "default.qss"),
    _primitive("first_run", "app", "first_run", "bool", True, prefix="is"),
    ConfigField("base_directory", "library", ("root_directory",), _decode_base_directory, _encode_base_directory),
    _primitive("scan_on_startup", "library", "scan_on_startup", "bool", True),
    _primitive("auto_refresh", "library", "auto_refresh", "bool", False),
    ConfigField("excluded_genres", "library", ("excluded_genres",), _decode_csv_list, _encode_csv_list),
    ConfigField("excluded_roles", "library", ("excluded_roles",), _decode_csv_list, _encode_csv_list),
    _primitive("volume", "playback", "volume", "int", 75),
    _primitive("shuffle", "playback", "shuffle", "bool", False),
    _primitive("repeat_mode", "playback", "repeat", "str", "none"),
    _primitive("output_device", "audio", "output_device", "str", "default"),
    _primitive("buffer_size", "audio", "buffer_size", "int", 1024),
    _primitive("exclusive_mode", "audio", "exclusive_mode", "bool", False),
    ConfigField("window_size", "window", ("size",), _decode_window_size, _encode_window_size),
    ConfigField("window_position", "window", ("position",), _decode_window_position, _encode_window_position),
    ConfigField("window_state", "window", ("state",), _decode_window_state, _encode_window_state),
    _primitive("window_maximized", "window", "maximized", "bool", False, prefix="is"),
    _primitive("console_logging_enabled", "logging", "console_enabled", "bool", True, prefix="is"),
    _primitive("file_logging_enabled", "logging", "file_enabled", "bool", True, prefix="is"),
    _primitive("max_file_size_mb", "logging", "max_file_size_mb", "int", 10),
    _primitive("backup_count", "logging", "backup_count", "int", 14),
    ConfigField("logging_level", "logging", ("level",), _decode_logging_level, _encode_logging_level),
    _primitive("equalizer_enabled", "equalizer", "enabled", "bool", False),
    _primitive("equalizer_custom_preset_name", "equalizer", "custom_preset_name", "str", "My Custom EQ"),
    ConfigField("equalizer_band_gains", "equalizer", ("band_gains",), _decode_band_gains, _encode_band_gains),
    _primitive("equalizer_presets", "equalizer", "presets", "list", "Flat,Bass Boost,Treble Boost,Rock,Pop,Jazz,Classical,Electronic,Hip Hop,Acoustic,Vocal Boost,Dance"),
    _primitive("display_theme", "display", "theme", "str", "dark_mode"),
    _primitive("ui_scale", "display", "ui_scale", "float", 1.0),
    _primitive("font_family", "display", "font_family", "str", "Inter"),
    _primitive("font_size", "display", "font_size", "int", 10),
    _primitive("blur_explicit_art", "display", "blur_explicit_art", "bool", False),
    _primitive("censor_explicit_words", "display", "censor_explicit_words", "bool", False),
    _primitive("menu_bar_auto_hide", "display", "menu_bar_auto_hide", "bool", False),
    _primitive("persist_queue", "queue", "persist_queue", "bool", True),
    _primitive("transcode_cache_max_mb", "sync", "transcode_cache_max_mb", "int", 2048),
    _primitive("waveform_cache_max_mb", "player", "waveform_cache_max_mb", "int", 256),
    _primitive("waveform_display_mode", "player", "display_mode", "str", "linear"),
    _primitive("track_view_visible_columns", "track_view", "visible_columns", "list", ""),
    _primitive("track_view_column_order", "track_view", "column_order", "list", ""),
    _primitive("track_view_column_widths", "track_view", "column_widths", "int_list", ""),
    ConfigField("lyrics_sync_offset", "nowplaying", ("lyrics_sync_offset",), _decode_lyrics_offset, _encode_lyrics_offset),
    _primitive("manual_sync_reaction_ms", "nowplaying", "manual_sync_reaction_ms", "int", 200),
    _primitive("influence_legend_visible", "influences", "legend_visible", "bool", True),
    ConfigField("influence_legend_size", "influences", ("legend_width", "legend_height"), _decode_legend_size, _encode_legend_size),
    ConfigField("influence_legend_position", "influences", ("legend_x", "legend_y"), _decode_legend_position, _encode_legend_position),
    _primitive("nav_item_order", "navigation", "item_order", "list", ""),
    _primitive("nav_hidden_items", "navigation", "hidden_items", "list", ""),
    ConfigField("album_view_filters", "album_view", ("filters",), _decode_json_dict, _encode_json_dict),
    ConfigField("artist_view_filters", "artist_view", ("filters",), _decode_json_dict, _encode_json_dict),
    _primitive("last_art_dir", "ui", "last_art_dir", "str", str(Path.home())),
]


def _install_config_field(cls, field: ConfigField):
    """Add the generated getter and setter for `field` to `cls`."""

    def getter(self, _field=field):
        return _field.decode(self, _field.section, _field.keys)

    def setter(self, *values, _field=field):
        _field.encode(self, _field.section, _field.keys, *values)

    getter.__name__ = f"{field.getter_prefix}_{field.name}"
    setter.__name__ = f"set_{field.name}"
    setattr(cls, getter.__name__, getter)
    setattr(cls, setter.__name__, setter)


for _field in _CONFIG_FIELDS:
    _install_config_field(Config, _field)


app_config = Config()
