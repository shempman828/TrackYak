"""Class for managing the startup screen and config creation."""

import configparser
import logging
from pathlib import Path

from PySide6.QtCore import QByteArray, QPoint, QSize

from src.asset_paths import config
from src.logger_config import logger

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
            except Exception as e:
                logger.error(f"Error loading config: {e}")
                self._create_default_config()
        else:
            self._create_default_config()
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

        self.config["queue"] = {
            "history_ids": "",  # most-recent-last, up to 500 entries
            "queue_ids": "",  # current + up to 500 upcoming
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

    def save(self):
        """Save configuration to file"""
        try:
            with open(self.config_path, "w") as configfile:
                self.config.write(configfile)
            logger.info(f"Configuration saved to {self.config_path}")
        except Exception as e:
            logger.error(f"Error saving config: {e}")

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
        self.config.set("window", "size", size_str)

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
        self.config.set("window", "position", pos_str)

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
        self.config.set("window", "state", state_b64)

    def is_window_maximized(self):
        """Check if window should be maximized"""
        return self.config.getboolean("window", "maximized", fallback=False)

    def set_window_maximized(self, maximized: bool):
        """Set window maximized state"""
        self.config.set("window", "maximized", str(maximized).lower())

    # App properties
    def get_theme(self):
        """Get current theme"""
        return self.config.get("app", "theme", fallback="dark_mode")

    def set_theme(self, theme: str):
        """Set theme"""
        self.config.set("app", "theme", theme)

    def get_theme_file(self):
        """Get current theme file"""
        return self.config.get("app", "theme_file", fallback="default.qss")

    def set_theme_file(self, theme_file: str):
        """Set theme file"""
        self.config.set("app", "theme_file", theme_file)

    def is_first_run(self):
        """Check if this is the first run"""
        return self.config.getboolean("app", "first_run", fallback=True)

    def set_first_run(self, first_run: bool):
        """Set first run flag"""
        self.config.set("app", "first_run", str(first_run).lower())

    # Base directory methods (replacing your deprecated code)
    def get_base_directory(self):
        """Get base music directory"""
        return Path(
            self.config.get(
                "library", "root_directory", fallback=str(Path.home() / "Music")
            )
        )

    def set_base_directory(self, directory: str | Path):
        """Set base music directory"""
        self.config.set("library", "root_directory", str(directory))

    def get_music_directory(self):
        """Get music directory (alias for base directory)"""
        return self.get_base_directory()

    def set_music_directory(self, directory: str | Path):
        """Set music directory (alias for base directory)"""
        self.set_base_directory(directory)

    # Library properties
    def get_scan_on_startup(self):
        """Get scan on startup setting"""
        return self.config.getboolean("library", "scan_on_startup", fallback=True)

    def set_scan_on_startup(self, scan: bool):
        """Set scan on startup setting"""
        self.config.set("library", "scan_on_startup", str(scan).lower())

    def get_auto_refresh(self):
        """Get auto refresh setting"""
        return self.config.getboolean("library", "auto_refresh", fallback=False)

    def set_auto_refresh(self, refresh: bool):
        """Set auto refresh setting"""
        self.config.set("library", "auto_refresh", str(refresh).lower())

    # Playback properties
    def get_volume(self):
        """Get volume setting"""
        return self.config.getint("playback", "volume", fallback=75)

    def set_volume(self, volume: int):
        """Set volume setting"""
        self.config.set("playback", "volume", str(volume))

    def get_shuffle(self):
        """Get shuffle setting"""
        return self.config.getboolean("playback", "shuffle", fallback=False)

    def set_shuffle(self, shuffle: bool):
        """Set shuffle setting"""
        self.config.set("playback", "shuffle", str(shuffle).lower())

    def get_repeat_mode(self):
        """Get repeat mode"""
        return self.config.get("playback", "repeat", fallback="none")

    def set_repeat_mode(self, mode: str):
        """Set repeat mode"""
        self.config.set("playback", "repeat", mode)

    def get_fade_duration(self):
        """Get fade duration"""
        return self.config.getint("playback", "fade_duration", fallback=0)

    def set_fade_duration(self, duration: int):
        """Set fade duration"""
        self.config.set("playback", "fade_duration", str(duration))

    def get_crossfade(self):
        """Get crossfade setting"""
        return self.config.getboolean("playback", "crossfade", fallback=False)

    def set_crossfade(self, crossfade: bool):
        """Set crossfade setting"""
        self.config.set("playback", "crossfade", str(crossfade).lower())

    # Audio properties
    def get_output_device(self):
        """Get output device"""
        return self.config.get("audio", "output_device", fallback="default")

    def set_output_device(self, device: str):
        """Set output device"""
        self.config.set("audio", "output_device", device)

    def get_sample_rate(self):
        """Get sample rate"""
        return self.config.getint("audio", "sample_rate", fallback=44100)

    def set_sample_rate(self, rate: int):
        """Set sample rate"""
        self.config.set("audio", "sample_rate", str(rate))

    def get_buffer_size(self):
        """Get buffer size"""
        return self.config.getint("audio", "buffer_size", fallback=1024)

    def set_buffer_size(self, size: int):
        """Set buffer size"""
        self.config.set("audio", "buffer_size", str(size))

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
        level_name = self.config.get("logging", "level", fallback="INFO")
        return getattr(logging, level_name, logging.INFO)

    def set_logging_level(self, level_name: str):
        """Set logging level"""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if level_name.upper() in valid_levels:
            self.config.set("logging", "level", level_name.upper())

    def is_console_logging_enabled(self):
        """Check if console logging is enabled"""
        return self.config.getboolean("logging", "console_enabled", fallback=True)

    def set_console_logging_enabled(self, enabled: bool):
        """Set console logging enabled"""
        self.config.set("logging", "console_enabled", str(enabled).lower())

    def is_file_logging_enabled(self):
        """Check if file logging is enabled"""
        return self.config.getboolean("logging", "file_enabled", fallback=True)

    def set_file_logging_enabled(self, enabled: bool):
        """Set file logging enabled"""
        self.config.set("logging", "file_enabled", str(enabled).lower())

    def get_max_file_size_mb(self):
        """Get maximum log file size in MB"""
        return self.config.getint("logging", "max_file_size_mb", fallback=10)

    def set_max_file_size_mb(self, size_mb: int):
        """Set maximum log file size in MB"""
        self.config.set("logging", "max_file_size_mb", str(size_mb))

    def get_backup_count(self):
        """Get number of backup log files to keep"""
        return self.config.getint("logging", "backup_count", fallback=14)

    def set_backup_count(self, count: int):
        """Set number of backup log files to keep"""
        self.config.set("logging", "backup_count", str(count))

    def get_equalizer_enabled(self):
        """Get equalizer enabled state"""
        return self.config.getboolean("equalizer", "enabled", fallback=False)

    def set_equalizer_enabled(self, enabled: bool):
        """Set equalizer enabled state"""
        self.config.set("equalizer", "enabled", str(enabled).lower())

    def get_equalizer_custom_preset_name(self):
        """Get custom preset name"""
        return self.config.get(
            "equalizer", "custom_preset_name", fallback="My Custom EQ"
        )

    def set_equalizer_custom_preset_name(self, name: str):
        """Set custom preset name"""
        self.config.set("equalizer", "custom_preset_name", name)

    def get_equalizer_band_gains(self):
        """Get band gains as list of floats"""
        gains_str = self.config.get(
            "equalizer",
            "band_gains",
            fallback="0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0,0.0",
        )
        try:
            return [float(gain) for gain in gains_str.split(",")]
        except:  # noqa: E722
            return [0.0] * 10

    def set_equalizer_band_gains(self, gains: list):
        """Set band gains from list of floats"""
        gains_str = ",".join(f"{gain:.1f}" for gain in gains)
        self.config.set("equalizer", "band_gains", gains_str)

    def get_equalizer_presets(self):
        """Get list of available presets"""
        presets_str = self.config.get(
            "equalizer",
            "presets",
            fallback="Flat,Bass Boost,Treble Boost,Rock,Pop,Jazz,Classical,Electronic,Hip Hop,Acoustic,Vocal Boost,Dance",
        )
        return presets_str.split(",")

    def set_equalizer_presets(self, presets: list):
        """Set available presets"""
        presets_str = ",".join(presets)
        self.config.set("equalizer", "presets", presets_str)

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
        return self.config.get("display", "theme", fallback="dark_mode")

    def set_display_theme(self, theme: str):
        """Set display theme"""
        self.config.set("display", "theme", theme)

    def get_ui_scale(self):
        """Get UI scale factor"""
        return self.config.getfloat("display", "ui_scale", fallback=1.0)

    def set_ui_scale(self, scale: float):
        """Set UI scale factor"""
        self.config.set("display", "ui_scale", str(scale))

    def get_font_family(self):
        """Get font family"""
        return self.config.get("display", "font_family", fallback="Inter")

    def set_font_family(self, font_family: str):
        """Set font family"""
        self.config.set("display", "font_family", font_family)

    def get_font_size(self):
        """Get font size"""
        return self.config.getint("display", "font_size", fallback=10)

    def set_font_size(self, size: int):
        """Set font size"""
        self.config.set("display", "font_size", str(size))

    # Queue properties
    def get_queue_track_ids(self):
        """Get saved queue track IDs as string."""
        return self.config.get("queue", "track_ids", fallback="")

    def set_queue_track_ids(self, track_ids_str: str):
        """Set queue track IDs."""
        self.config.set("queue", "track_ids", track_ids_str)

    def get_queue_history_exists(self):
        """Get queue history state."""
        return self.config.getboolean("queue", "history_exists", fallback=False)

    def set_queue_history_exists(self, exists: bool):
        """Set queue history state."""
        self.config.set("queue", "history_exists", str(exists).lower())

    def get_persist_queue(self):
        """Get whether to persist queue across sessions."""
        return self.config.getboolean("queue", "persist_queue", fallback=True)

    def set_persist_queue(self, persist: bool):
        """Set whether to persist queue across sessions."""
        self.config.set("queue", "persist_queue", str(persist).lower())

    def get_track_view_visible_columns(self):
        """Get visible columns from config."""
        columns_str = self.config.get("track_view", "visible_columns", fallback="")
        if columns_str:
            return columns_str.split(",")
        return []

    def set_track_view_visible_columns(self, columns: list):
        """Set visible columns in config."""
        self.config.set("track_view", "visible_columns", ",".join(columns))

    def get_track_view_column_order(self):
        """Get column order from config."""
        order_str = self.config.get("track_view", "column_order", fallback="")
        if order_str:
            return order_str.split(",")
        return []

    def set_track_view_column_order(self, order: list):
        """Set column order in config."""
        self.config.set("track_view", "column_order", ",".join(order))

    def get_track_view_column_widths(self):
        """Get column widths from config."""
        widths_str = self.config.get("track_view", "column_widths", fallback="")
        if widths_str:
            return [int(w) for w in widths_str.split(",")]
        return []

    def set_track_view_column_widths(self, widths: list):
        """Set column widths in config."""
        widths_str = ",".join(str(w) for w in widths)
        self.config.set("track_view", "column_widths", widths_str)

    def get_lyrics_sync_offset(self) -> int:
        """
        Get the saved lyrics sync-offset slider value.
        The value is stored as tenths of a second (e.g. -5 = −0.5 s).
        Returns an int in the range the slider accepts (−50 … 50).
        """
        try:
            return self.config.getint("nowplaying", "lyrics_sync_offset", fallback=-5)
        except Exception:
            return -5

    def set_lyrics_sync_offset(self, value: int):
        """
        Persist the lyrics sync-offset slider value.
        value is in tenths of a second (same unit the slider uses).
        """
        if "nowplaying" not in self.config:
            self.config["nowplaying"] = {}
        self.config.set("nowplaying", "lyrics_sync_offset", str(int(value)))

    def get_last_art_dir(self) -> str:
        """Get the last directory used when picking album artwork."""
        return self.config.get("ui", "last_art_dir", fallback=str(Path.home()))

    def set_last_art_dir(self, directory: str) -> None:
        """Persist the last directory used when picking album artwork."""
        if "ui" not in self.config:
            self.config["ui"] = {}
        self.config.set("ui", "last_art_dir", directory)


app_config = Config()
