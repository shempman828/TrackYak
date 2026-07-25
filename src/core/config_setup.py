"""Class for managing the startup screen and config creation."""

import configparser
import json
import logging
from pathlib import Path

from PySide6.QtCore import QByteArray, QPoint, QSize

from src.core.asset_paths import config
from src.core.logger_config import logger

config_dir = config("config.ini")


class Config:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
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
            except Exception as e:
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
        except Exception as e:
            logger.error(f"Error saving config: {e}")

    # --- Generic section/type helpers -------------------------------------
    # Every get_*/set_* pair below is a thin wrapper around one of these.
    # Setters ensure the section exists first, since not all sections are
    # guaranteed to be present in configs written by older app versions.

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

    # Window properties
    def get_window_size(self):
        """Get window size from config"""
        try:
            size_str = self.config.get("window", "size", fallback="1280,720")
            width, height = map(int, size_str.split(","))
            return QSize(width, height)
        except:  # noqa: E722
            return QSize(1280, 720)

    def set_window_size(self, size: QSize):
        """Set window size in config"""
        size_str = f"{size.width()},{size.height()}"
        self._set_str("window", "size", size_str)

    def get_window_position(self):
        """Get window position from config"""
        try:
            pos_str = self.config.get("window", "position", fallback="100,100")
            x, y = map(int, pos_str.split(","))
            return QPoint(x, y)
        except:  # noqa: E722
            return QPoint(100, 100)

    def set_window_position(self, position: QPoint):
        """Set window position in config"""
        pos_str = f"{position.x()},{position.y()}"
        self._set_str("window", "position", pos_str)

    def get_window_state(self):
        """Get window state from config"""
        state_b64 = self.config.get("window", "state", fallback="")
        if state_b64:
            try:
                return QByteArray.fromBase64(state_b64.encode())
            except:  # noqa: E722
                return QByteArray()
        return QByteArray()

    def set_window_state(self, state: QByteArray):
        """Set window state in config"""
        state_b64 = state.toBase64().data().decode()
        self._set_str("window", "state", state_b64)

    def is_window_maximized(self):
        """Check if window should be maximized"""
        return self._get_bool("window", "maximized", fallback=False)

    def set_window_maximized(self, maximized: bool):
        """Set window maximized state"""
        self._set_bool("window", "maximized", maximized)

    # App properties
    def get_theme(self):
        """Get current theme"""
        return self._get_str("app", "theme", fallback="dark_mode")

    def set_theme(self, theme: str):
        """Set theme"""
        self._set_str("app", "theme", theme)

    def get_theme_file(self):
        """Get current theme file"""
        return self._get_str("app", "theme_file", fallback="default.qss")

    def set_theme_file(self, theme_file: str):
        """Set theme file"""
        self._set_str("app", "theme_file", theme_file)

    def is_first_run(self):
        """Check if this is the first run"""
        return self._get_bool("app", "first_run", fallback=True)

    def set_first_run(self, first_run: bool):
        """Set first run flag"""
        self._set_bool("app", "first_run", first_run)

    # Base directory methods (replacing your deprecated code)
    def get_base_directory(self):
        """Get base music directory"""
        return Path(
            self._get_str(
                "library", "root_directory", fallback=str(Path.home() / "Music")
            )
        )

    def set_base_directory(self, directory: str | Path):
        """Set base music directory"""
        self._set_str("library", "root_directory", str(directory))

    def get_music_directory(self):
        """Get music directory (alias for base directory)"""
        return self.get_base_directory()

    def set_music_directory(self, directory: str | Path):
        """Set music directory (alias for base directory)"""
        self.set_base_directory(directory)

    # Library properties
    def get_scan_on_startup(self):
        """Get scan on startup setting"""
        return self._get_bool("library", "scan_on_startup", fallback=True)

    def set_scan_on_startup(self, scan: bool):
        """Set scan on startup setting"""
        self._set_bool("library", "scan_on_startup", scan)

    def get_auto_refresh(self):
        """Get auto refresh setting"""
        return self._get_bool("library", "auto_refresh", fallback=False)

    def set_auto_refresh(self, refresh: bool):
        """Set auto refresh setting"""
        self._set_bool("library", "auto_refresh", refresh)

    def get_excluded_genres(self) -> list:
        """Get genre names that should never be created during import."""
        raw = self._get_str("library", "excluded_genres", fallback="")
        return [g.strip() for g in raw.split(",") if g.strip()]

    def set_excluded_genres(self, genres: list):
        """Set genre names that should never be created during import."""
        self._set_str(
            "library", "excluded_genres", ",".join(g.strip() for g in genres if g.strip())
        )

    # Playback properties
    def get_volume(self):
        """Get volume setting"""
        return self._get_int("playback", "volume", fallback=75)

    def set_volume(self, volume: int):
        """Set volume setting"""
        self._set_int("playback", "volume", volume)

    def get_shuffle(self):
        """Get shuffle setting"""
        return self._get_bool("playback", "shuffle", fallback=False)

    def set_shuffle(self, shuffle: bool):
        """Set shuffle setting"""
        self._set_bool("playback", "shuffle", shuffle)

    def get_repeat_mode(self):
        """Get repeat mode"""
        return self._get_str("playback", "repeat", fallback="none")

    def set_repeat_mode(self, mode: str):
        """Set repeat mode"""
        self._set_str("playback", "repeat", mode)

    def get_fade_duration(self):
        """Get fade duration in seconds (0.1s granularity)"""
        return self._get_float("playback", "fade_duration", fallback=0.0)

    def set_fade_duration(self, duration: float):
        """Set fade duration in seconds (0.1s granularity)"""
        self._set_float("playback", "fade_duration", duration)

    def get_crossfade(self):
        """Get crossfade setting"""
        return self._get_bool("playback", "crossfade", fallback=False)

    def set_crossfade(self, crossfade: bool):
        """Set crossfade setting"""
        self._set_bool("playback", "crossfade", crossfade)

    # Audio properties
    def get_output_device(self):
        """Get output device"""
        return self._get_str("audio", "output_device", fallback="default")

    def set_output_device(self, device: str):
        """Set output device"""
        self._set_str("audio", "output_device", device)

    def get_sample_rate(self):
        """Get sample rate"""
        return self._get_int("audio", "sample_rate", fallback=44100)

    def set_sample_rate(self, rate: int):
        """Set sample rate"""
        self._set_int("audio", "sample_rate", rate)

    def get_buffer_size(self):
        """Get buffer size"""
        return self._get_int("audio", "buffer_size", fallback=1024)

    def set_buffer_size(self, size: int):
        """Set buffer size"""
        self._set_int("audio", "buffer_size", size)

    def get_exclusive_mode(self):
        """Get bit-perfect/exclusive output mode"""
        return self._get_bool("audio", "exclusive_mode", fallback=False)

    def set_exclusive_mode(self, enabled: bool):
        """Set bit-perfect/exclusive output mode"""
        self._set_bool("audio", "exclusive_mode", enabled)

    # Theme management
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
            except Exception as e:
                logger.error(f"Error loading theme {theme_path}: {e}")
        return ""

    # Logging properties
    def get_logging_level(self):
        """Get logging level"""
        level_name = self._get_str("logging", "level", fallback="INFO")
        return getattr(logging, level_name, logging.INFO)

    def set_logging_level(self, level_name: str):
        """Set logging level"""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if level_name.upper() in valid_levels:
            self._set_str("logging", "level", level_name.upper())

    def is_console_logging_enabled(self):
        """Check if console logging is enabled"""
        return self._get_bool("logging", "console_enabled", fallback=True)

    def set_console_logging_enabled(self, enabled: bool):
        """Set console logging enabled"""
        self._set_bool("logging", "console_enabled", enabled)

    def is_file_logging_enabled(self):
        """Check if file logging is enabled"""
        return self._get_bool("logging", "file_enabled", fallback=True)

    def set_file_logging_enabled(self, enabled: bool):
        """Set file logging enabled"""
        self._set_bool("logging", "file_enabled", enabled)

    def get_max_file_size_mb(self):
        """Get maximum log file size in MB"""
        return self._get_int("logging", "max_file_size_mb", fallback=10)

    def set_max_file_size_mb(self, size_mb: int):
        """Set maximum log file size in MB"""
        self._set_int("logging", "max_file_size_mb", size_mb)

    def get_backup_count(self):
        """Get number of backup log files to keep"""
        return self._get_int("logging", "backup_count", fallback=14)

    def set_backup_count(self, count: int):
        """Set number of backup log files to keep"""
        self._set_int("logging", "backup_count", count)

    def get_equalizer_enabled(self):
        """Get equalizer enabled state"""
        return self._get_bool("equalizer", "enabled", fallback=False)

    def set_equalizer_enabled(self, enabled: bool):
        """Set equalizer enabled state"""
        self._set_bool("equalizer", "enabled", enabled)

    def get_equalizer_custom_preset_name(self):
        """Get custom preset name"""
        return self._get_str("equalizer", "custom_preset_name", fallback="My Custom EQ")

    def set_equalizer_custom_preset_name(self, name: str):
        """Set custom preset name"""
        self._set_str("equalizer", "custom_preset_name", name)

    def get_equalizer_band_gains(self):
        """Get band gains as list of floats"""
        gains_str = self._get_str(
            "equalizer",
            "band_gains",
            fallback="0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0",
        )
        try:
            return [float(gain) for gain in gains_str.split(",")]
        except ValueError:
            return [0.0] * 10

    def set_equalizer_band_gains(self, gains: list):
        """Set band gains from list of floats"""
        gains_str = ",".join(f"{gain:.1f}" for gain in gains)
        self._set_str("equalizer", "band_gains", gains_str)

    def get_equalizer_presets(self):
        """Get list of available presets"""
        return self._get_list(
            "equalizer",
            "presets",
            fallback="Flat,Bass Boost,Treble Boost,Rock,Pop,Jazz,Classical,Electronic,Hip Hop,Acoustic,Vocal Boost,Dance",
        )

    def set_equalizer_presets(self, presets: list):
        """Set available presets"""
        self._set_list("equalizer", "presets", presets)

    def save_equalizer_settings(
        self, enabled: bool, band_gains: list, preset_name: str = "Custom"
    ):
        """Convenience method to save all EQ settings at once"""
        self.set_equalizer_enabled(enabled)
        self.set_equalizer_band_gains(band_gains)
        if preset_name != "Custom":
            self.set_equalizer_custom_preset_name(preset_name)
        self.save()

    def get_display_theme(self):
        """Get display theme"""
        return self._get_str("display", "theme", fallback="dark_mode")

    def set_display_theme(self, theme: str):
        """Set display theme"""
        self._set_str("display", "theme", theme)

    def get_ui_scale(self):
        """Get UI scale factor"""
        return self._get_float("display", "ui_scale", fallback=1.0)

    def set_ui_scale(self, scale: float):
        """Set UI scale factor"""
        self._set_float("display", "ui_scale", scale)

    def get_font_family(self):
        """Get font family"""
        return self._get_str("display", "font_family", fallback="Inter")

    def set_font_family(self, font_family: str):
        """Set font family"""
        self._set_str("display", "font_family", font_family)

    def get_font_size(self):
        """Get font size"""
        return self._get_int("display", "font_size", fallback=10)

    def set_font_size(self, size: int):
        """Set font size"""
        self._set_int("display", "font_size", size)

    def get_blur_explicit_art(self):
        """Get whether album art marked explicit should be shown blurred"""
        return self._get_bool("display", "blur_explicit_art", fallback=False)

    def set_blur_explicit_art(self, enabled: bool):
        """Set whether album art marked explicit should be shown blurred"""
        self._set_bool("display", "blur_explicit_art", enabled)

    def get_censor_explicit_words(self):
        """Get whether explicit words should be censored throughout the app"""
        return self._get_bool("display", "censor_explicit_words", fallback=False)

    def set_censor_explicit_words(self, enabled: bool):
        """Set whether explicit words should be censored throughout the app"""
        self._set_bool("display", "censor_explicit_words", enabled)

    # Queue properties
    def get_persist_queue(self):
        """Get whether to persist queue across sessions."""
        return self._get_bool("queue", "persist_queue", fallback=True)

    def set_persist_queue(self, persist: bool):
        """Set whether to persist queue across sessions."""
        self._set_bool("queue", "persist_queue", persist)

    def get_track_view_visible_columns(self):
        """Get visible columns from config."""
        return self._get_list("track_view", "visible_columns", fallback="")

    def set_track_view_visible_columns(self, columns: list):
        """Set visible columns in config."""
        self._set_list("track_view", "visible_columns", columns)

    def get_track_view_column_order(self):
        """Get column order from config."""
        return self._get_list("track_view", "column_order", fallback="")

    def set_track_view_column_order(self, order: list):
        """Set column order in config."""
        self._set_list("track_view", "column_order", order)

    def get_track_view_column_widths(self):
        """Get column widths from config."""
        return self._get_int_list("track_view", "column_widths", fallback="")

    def set_track_view_column_widths(self, widths: list):
        """Set column widths in config."""
        self._set_list("track_view", "column_widths", widths)

    def get_lyrics_sync_offset(self) -> int:
        """
        Get the saved lyrics sync-offset slider value.
        The value is stored as tenths of a second (e.g. -5 = −0.5 s).
        Returns an int in the range the slider accepts (−50 … 50).
        """
        try:
            return self._get_int("nowplaying", "lyrics_sync_offset", fallback=-5)
        except Exception:
            return -5

    def set_lyrics_sync_offset(self, value: int):
        """
        Persist the lyrics sync-offset slider value.
        value is in tenths of a second (same unit the slider uses).
        """
        self._set_int("nowplaying", "lyrics_sync_offset", int(value))

    def get_influence_legend_visible(self) -> bool:
        """Get whether the cluster legend overlay is shown in the influences view."""
        return self._get_bool("influences", "legend_visible", fallback=True)

    def set_influence_legend_visible(self, visible: bool):
        """Set whether the cluster legend overlay is shown in the influences view."""
        self._set_bool("influences", "legend_visible", visible)

    def get_influence_legend_size(self) -> tuple:
        """Get the persisted (width, height) of the cluster legend overlay."""
        width = self._get_int("influences", "legend_width", fallback=240)
        height = self._get_int("influences", "legend_height", fallback=260)
        return width, height

    def set_influence_legend_size(self, width: int, height: int):
        """Persist the (width, height) of the cluster legend overlay."""
        self._set_int("influences", "legend_width", int(width))
        self._set_int("influences", "legend_height", int(height))

    def get_influence_legend_position(self):
        """Get the persisted (x, y) of the cluster legend overlay, or None if
        the user has never dragged it (falls back to the default bottom-left
        anchor in that case)."""
        if not self.config.has_option("influences", "legend_x") or not self.config.has_option(
            "influences", "legend_y"
        ):
            return None
        return self._get_int("influences", "legend_x", fallback=0), self._get_int(
            "influences", "legend_y", fallback=0
        )

    def set_influence_legend_position(self, x: int, y: int):
        """Persist the (x, y) of the cluster legend overlay after a manual drag."""
        self._set_int("influences", "legend_x", int(x))
        self._set_int("influences", "legend_y", int(y))

    def get_influence_cluster_names(self) -> dict:
        """Get persisted Louvain cluster names, keyed by anchor artist ID (as string).

        Community indices are reassigned on every Louvain recompute, so names
        are pinned to each cluster's highest-degree "anchor" artist instead.
        """
        raw = self._get_str("influences", "cluster_names", fallback="")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_influence_cluster_names(self, names: dict):
        """Set persisted Louvain cluster names, keyed by anchor artist ID (as string)."""
        self._set_str("influences", "cluster_names", json.dumps(names))

    def get_last_art_dir(self) -> str:
        """Get the last directory used when picking album artwork."""
        return self._get_str("ui", "last_art_dir", fallback=str(Path.home()))

    def set_last_art_dir(self, directory: str) -> None:
        """Persist the last directory used when picking album artwork."""
        self._set_str("ui", "last_art_dir", directory)


app_config = Config()
