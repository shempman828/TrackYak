"""Class for managing the startup screen and config creation."""

import configparser
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QPoint, QSize

from src.core.asset_paths import config
from src.core.logger_config import logger

config_dir = config("config.ini")


@dataclass(frozen=True)
class ConfigField:
    """Declarative description of one config-backed get_*/set_* accessor pair.

    `decode`/`encode` are plain functions of the form
    `decode(self, section, keys) -> value` / `encode(self, section, keys, *values)`.
    `*values` mirrors the positional args the set_* method takes — one for
    most fields, two for the handful (legend size/position) that store a pair.
    Simple fields share the generic codecs built by `_primitive`; fields with
    non-trivial storage (multi-key, JSON, Qt types, validation) supply their
    own small decode/encode pair below instead of a hand-written method body.
    """

    name: str
    section: str
    keys: tuple
    decode: Callable[["Config", str, tuple], Any]
    encode: Callable[..., None]
    getter_prefix: str = "get"


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
        return getattr(self, getter_name)(section, keys[0], fallback=default)

    def encode(self, section, keys, *values):
        getattr(self, setter_name)(section, keys[0], values[0])

    return ConfigField(name, section, (key,), decode, encode, prefix)


# --- Custom codecs for fields that aren't a single primitive value ---------


def _decode_base_directory(self, section, keys):
    return Path(self._get_str(section, keys[0], fallback=str(Path.home() / "Music")))


def _encode_base_directory(self, section, keys, *values):
    self._set_str(section, keys[0], str(values[0]))


def _decode_excluded_genres(self, section, keys):
    raw = self._get_str(section, keys[0], fallback="")
    return [g.strip() for g in raw.split(",") if g.strip()]


def _encode_excluded_genres(self, section, keys, *values):
    self._set_str(section, keys[0], ",".join(g.strip() for g in values[0] if g.strip()))


def _decode_window_size(self, section, keys):
    try:
        size_str = self.config.get(section, keys[0], fallback="1280,720")
        width, height = map(int, size_str.split(","))
        return QSize(width, height)
    except ValueError:
        return QSize(1280, 720)


def _encode_window_size(self, section, keys, *values):
    value = values[0]
    self._set_str(section, keys[0], f"{value.width()},{value.height()}")


def _decode_window_position(self, section, keys):
    try:
        pos_str = self.config.get(section, keys[0], fallback="100,100")
        x, y = map(int, pos_str.split(","))
        return QPoint(x, y)
    except ValueError:
        return QPoint(100, 100)


def _encode_window_position(self, section, keys, *values):
    value = values[0]
    self._set_str(section, keys[0], f"{value.x()},{value.y()}")


def _decode_window_state(self, section, keys):
    state_b64 = self.config.get(section, keys[0], fallback="")
    if state_b64:
        try:
            return QByteArray.fromBase64(state_b64.encode())
        except ValueError:
            return QByteArray()
    return QByteArray()


def _encode_window_state(self, section, keys, *values):
    self._set_str(section, keys[0], values[0].toBase64().data().decode())


def _decode_logging_level(self, section, keys):
    level_name = self._get_str(section, keys[0], fallback="INFO")
    return getattr(logging, level_name, logging.INFO)


def _encode_logging_level(self, section, keys, *values):
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    value = values[0]
    if value.upper() in valid_levels:
        self._set_str(section, keys[0], value.upper())


def _decode_band_gains(self, section, keys):
    gains_str = self._get_str(
        section, keys[0], fallback="0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0"
    )
    try:
        return [float(gain) for gain in gains_str.split(",")]
    except ValueError:
        return [0.0] * 10


def _encode_band_gains(self, section, keys, *values):
    self._set_str(section, keys[0], ",".join(f"{gain:.1f}" for gain in values[0]))


def _decode_lyrics_offset(self, section, keys):
    try:
        return self._get_int(section, keys[0], fallback=-5)
    except ValueError:
        return -5


def _encode_lyrics_offset(self, section, keys, *values):
    self._set_int(section, keys[0], int(values[0]))


def _decode_legend_size(self, section, keys):
    width_key, height_key = keys
    width = self._get_int(section, width_key, fallback=240)
    height = self._get_int(section, height_key, fallback=260)
    return width, height


def _encode_legend_size(self, section, keys, *values):
    width_key, height_key = keys
    width, height = values
    self._set_int(section, width_key, int(width))
    self._set_int(section, height_key, int(height))


def _decode_legend_position(self, section, keys):
    x_key, y_key = keys
    if not self.config.has_option(section, x_key) or not self.config.has_option(
        section, y_key
    ):
        return None
    return self._get_int(section, x_key, fallback=0), self._get_int(
        section, y_key, fallback=0
    )


def _encode_legend_position(self, section, keys, *values):
    x_key, y_key = keys
    x, y = values
    self._set_int(section, x_key, int(x))
    self._set_int(section, y_key, int(y))


def _decode_json_dict(self, section, keys):
    raw = self._get_str(section, keys[0], fallback="")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _encode_json_dict(self, section, keys, *values):
    self._set_str(section, keys[0], json.dumps(values[0]))


class Config:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            # Use asset_paths if no custom path provided
            self.config_path = Path(config("config.ini"))
            self.config = configparser.ConfigParser()
            self.themes_dir = Path("themes")
            self._ensure_themes_dir()
            self._ensure_config_dir()  # Create config directory if needed
            self.load()
            self._initialized = True

    def _ensure_config_dir(self):
        """Create config directory if it doesn't exist"""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

    def _ensure_themes_dir(self):
        """Create themes directory if it doesn't exist"""
        self.themes_dir.mkdir(exist_ok=True)

    def load(self):
        """Load configuration from file, create default if not exists"""
        if self.config_path.exists():
            try:
                self.config.read(self.config_path)
                logger.info(f"Configuration loaded from {self.config_path}")
                self._migrate_legacy_queue_keys()
            except (configparser.Error, OSError) as e:
                logger.error(f"Error loading config: {e}")
                self._create_default_config()
        else:
            self._create_default_config()
            self.save()

    def _migrate_legacy_queue_keys(self):
        """One-time cleanup: queue/history track IDs used to be stored directly
        in this ini file and could grow to tens of thousands of entries for a
        shuffled library. They now live in queue_state.json — strip the old
        keys so an already-bloated config.ini shrinks back down."""
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
        """Create default configuration structure"""
        # Window section
        self.config["window"] = {
            "size": "1280,720",
            "position": "100,100",
            "state": "",
            "maximized": "false",
        }
        # Display section (NEW)
        self.config["display"] = {
            "theme": "dark_mode",
            "ui_scale": "1.0",
            "font_family": "Inter",
            "font_size": "10",
            "blur_explicit_art": "false",
            "censor_explicit_words": "false",
        }
        # App section
        self.config["app"] = {
            "music_dir": str(Path.home() / "Music"),
            "first_run": "true",
            # Note: theme_file is now in display section
        }

        # Library section
        self.config["library"] = {
            "root_directory": str(Path.home() / "Music"),
            "scan_on_startup": "true",
            "auto_refresh": "false",
            "excluded_genres": "",
        }

        # Playback section
        self.config["playback"] = {
            "volume": "75",
            "shuffle": "false",
            "repeat": "none",  # none, one, all
            "fade_duration": "0",
            "crossfade": "false",
        }

        # Audio section
        self.config["audio"] = {
            "output_device": "default",
            "sample_rate": "44100",
            "buffer_size": "1024",
            "exclusive_mode": "false",
        }

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
            "presets": "Flat,Bass Boost,Treble Boost,Rock,Pop,Jazz,Classical,Electronic,Hip Hop,Acoustic,Vocal Boost,Dance",
        }
        # Queue section
        # Note: queue/history track IDs live in queue_state.json, not here —
        # a shuffled full-library queue can be tens of thousands of IDs, which
        # doesn't belong in a human-editable settings file.
        self.config["queue"] = {
            "persist_queue": "true",
        }
        self.config["track_view"] = {
            "visible_columns": "track_file_name,artist_name,album_name,title,genre,duration,year",
            "column_order": "track_file_name,artist_name,album_name,title,genre,duration,year",
            "column_widths": "",
        }
        self.config["album_view"] = {
            "filters": "",
        }
        self.config["artist_view"] = {
            "filters": "",
        }
        self.config["nowplaying"] = {
            "lyrics_sync_offset": "-5",  # stored as tenths of a second (int)
        }
        self.config["influences"] = {
            "legend_visible": "true",
            "cluster_names": "",
        }

    def save(self):
        """Save configuration to file"""
        try:
            with open(self.config_path, "w") as configfile:
                self.config.write(configfile)
            logger.info(f"Configuration saved to {self.config_path}")
        except OSError as e:
            logger.error(f"Error saving config: {e}")

    # --- Generic section/type helpers -------------------------------------
    # Every field in _CONFIG_FIELDS below is a thin wrapper around one of
    # these. Setters ensure the section exists first, since not all sections
    # are guaranteed to be present in configs written by older app versions.

    def _ensure_section(self, section: str):
        if section not in self.config:
            self.config[section] = {}

    def _get_str(self, section: str, key: str, fallback: str = "") -> str:
        return self.config.get(section, key, fallback=fallback)

    def _set_str(self, section: str, key: str, value: str):
        self._ensure_section(section)
        self.config.set(section, key, value)

    def _get_bool(self, section: str, key: str, fallback: bool = False) -> bool:
        return self.config.getboolean(section, key, fallback=fallback)

    def _set_bool(self, section: str, key: str, value: bool):
        self._set_str(section, key, str(value).lower())

    def _get_int(self, section: str, key: str, fallback: int = 0) -> int:
        return self.config.getint(section, key, fallback=fallback)

    def _set_int(self, section: str, key: str, value: int):
        self._set_str(section, key, str(value))

    def _get_float(self, section: str, key: str, fallback: float = 0.0) -> float:
        return self.config.getfloat(section, key, fallback=fallback)

    def _set_float(self, section: str, key: str, value: float):
        self._set_str(section, key, str(value))

    def _get_list(self, section: str, key: str, fallback: str = "") -> list:
        raw = self.config.get(section, key, fallback=fallback)
        return raw.split(",") if raw else []

    def _set_list(self, section: str, key: str, items: list):
        self._set_str(section, key, ",".join(str(item) for item in items))

    def _get_int_list(self, section: str, key: str, fallback: str = "") -> list:
        raw = self.config.get(section, key, fallback=fallback)
        return [int(item) for item in raw.split(",")] if raw else []

    # Base directory has an alias name in addition to its generated
    # get_base_directory/set_base_directory (see _CONFIG_FIELDS below).
    def get_music_directory(self):
        """Get music directory (alias for base directory)"""
        return self.get_base_directory()

    def set_music_directory(self, directory: str | Path):
        """Set music directory (alias for base directory)"""
        self.set_base_directory(directory)

    # Theme management (filesystem-backed, not config-value accessors)
    def get_available_themes(self):
        """Get list of available theme files"""
        theme_files = []
        if self.themes_dir.exists():
            for file in self.themes_dir.glob("*.qss"):
                theme_files.append(file.name)
        return theme_files

    def get_theme_path(self, theme_file=None):
        """Get full path to theme file"""
        if theme_file is None:
            theme_file = self.get_theme_file()
        return self.themes_dir / theme_file

    def load_theme_stylesheet(self, theme_file=None):
        """Load QSS stylesheet from theme file"""
        theme_path = self.get_theme_path(theme_file)
        if theme_path.exists():
            try:
                with open(theme_path, "r", encoding="utf-8") as f:
                    return f.read()
            except (OSError, UnicodeDecodeError) as e:
                logger.error(f"Error loading theme {theme_path}: {e}")
        return ""

    def save_equalizer_settings(
        self, enabled: bool, band_gains: list, preset_name: str = "Custom"
    ):
        """Convenience method to save all EQ settings at once"""
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
    _primitive("theme", "app", "theme", "str", "dark_mode"),
    _primitive("theme_file", "app", "theme_file", "str", "default.qss"),
    _primitive("first_run", "app", "first_run", "bool", True, prefix="is"),
    ConfigField(
        "base_directory",
        "library",
        ("root_directory",),
        _decode_base_directory,
        _encode_base_directory,
    ),
    _primitive("scan_on_startup", "library", "scan_on_startup", "bool", True),
    _primitive("auto_refresh", "library", "auto_refresh", "bool", False),
    ConfigField(
        "excluded_genres",
        "library",
        ("excluded_genres",),
        _decode_excluded_genres,
        _encode_excluded_genres,
    ),
    _primitive("volume", "playback", "volume", "int", 75),
    _primitive("shuffle", "playback", "shuffle", "bool", False),
    _primitive("repeat_mode", "playback", "repeat", "str", "none"),
    _primitive("fade_duration", "playback", "fade_duration", "float", 0.0),
    _primitive("crossfade", "playback", "crossfade", "bool", False),
    _primitive("output_device", "audio", "output_device", "str", "default"),
    _primitive("sample_rate", "audio", "sample_rate", "int", 44100),
    _primitive("buffer_size", "audio", "buffer_size", "int", 1024),
    _primitive("exclusive_mode", "audio", "exclusive_mode", "bool", False),
    ConfigField(
        "window_size", "window", ("size",), _decode_window_size, _encode_window_size
    ),
    ConfigField(
        "window_position",
        "window",
        ("position",),
        _decode_window_position,
        _encode_window_position,
    ),
    ConfigField(
        "window_state",
        "window",
        ("state",),
        _decode_window_state,
        _encode_window_state,
    ),
    _primitive("window_maximized", "window", "maximized", "bool", False, prefix="is"),
    _primitive(
        "console_logging_enabled",
        "logging",
        "console_enabled",
        "bool",
        True,
        prefix="is",
    ),
    _primitive(
        "file_logging_enabled", "logging", "file_enabled", "bool", True, prefix="is"
    ),
    _primitive("max_file_size_mb", "logging", "max_file_size_mb", "int", 10),
    _primitive("backup_count", "logging", "backup_count", "int", 14),
    ConfigField(
        "logging_level",
        "logging",
        ("level",),
        _decode_logging_level,
        _encode_logging_level,
    ),
    _primitive("equalizer_enabled", "equalizer", "enabled", "bool", False),
    _primitive(
        "equalizer_custom_preset_name",
        "equalizer",
        "custom_preset_name",
        "str",
        "My Custom EQ",
    ),
    ConfigField(
        "equalizer_band_gains",
        "equalizer",
        ("band_gains",),
        _decode_band_gains,
        _encode_band_gains,
    ),
    _primitive(
        "equalizer_presets",
        "equalizer",
        "presets",
        "list",
        "Flat,Bass Boost,Treble Boost,Rock,Pop,Jazz,Classical,Electronic,Hip Hop,Acoustic,Vocal Boost,Dance",
    ),
    _primitive("display_theme", "display", "theme", "str", "dark_mode"),
    _primitive("ui_scale", "display", "ui_scale", "float", 1.0),
    _primitive("font_family", "display", "font_family", "str", "Inter"),
    _primitive("font_size", "display", "font_size", "int", 10),
    _primitive("blur_explicit_art", "display", "blur_explicit_art", "bool", False),
    _primitive(
        "censor_explicit_words", "display", "censor_explicit_words", "bool", False
    ),
    _primitive("persist_queue", "queue", "persist_queue", "bool", True),
    _primitive(
        "track_view_visible_columns", "track_view", "visible_columns", "list", ""
    ),
    _primitive("track_view_column_order", "track_view", "column_order", "list", ""),
    _primitive(
        "track_view_column_widths", "track_view", "column_widths", "int_list", ""
    ),
    ConfigField(
        "lyrics_sync_offset",
        "nowplaying",
        ("lyrics_sync_offset",),
        _decode_lyrics_offset,
        _encode_lyrics_offset,
    ),
    _primitive(
        "influence_legend_visible", "influences", "legend_visible", "bool", True
    ),
    ConfigField(
        "influence_legend_size",
        "influences",
        ("legend_width", "legend_height"),
        _decode_legend_size,
        _encode_legend_size,
    ),
    ConfigField(
        "influence_legend_position",
        "influences",
        ("legend_x", "legend_y"),
        _decode_legend_position,
        _encode_legend_position,
    ),
    ConfigField(
        "influence_cluster_names",
        "influences",
        ("cluster_names",),
        _decode_json_dict,
        _encode_json_dict,
    ),
    ConfigField(
        "album_view_filters",
        "album_view",
        ("filters",),
        _decode_json_dict,
        _encode_json_dict,
    ),
    ConfigField(
        "artist_view_filters",
        "artist_view",
        ("filters",),
        _decode_json_dict,
        _encode_json_dict,
    ),
    _primitive("last_art_dir", "ui", "last_art_dir", "str", str(Path.home())),
]


def _install_config_field(cls, field: ConfigField):
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
