"""Absolute paths to the app's asset, data, config and cache directories."""

from pathlib import Path
import shutil
import sys

from PySide6.QtGui import QIcon

# --- Base Directories --------------------------------------------------------

_FROZEN = getattr(sys, "frozen", False)
# Read-only bundled files. In a frozen (PyInstaller) build this is the temp extract dir.
BASE_DIR = Path(sys._MEIPASS) if _FROZEN else Path(__file__).resolve().parents[2]
# Writable user data. Must persist across runs, so a frozen build uses the executable's dir, not _MEIPASS.
USER_DIR = Path(sys.executable).resolve().parent if _FROZEN else BASE_DIR

# --- Core Directories --------------------------------------------------------

ASSETS_DIR = BASE_DIR / "assets"
THEMES_DIR = BASE_DIR / "themes"
IMAGES_DIR = USER_DIR / "images"
LOGS_DIR = USER_DIR / "logs"
PLAYLISTS_DIR = USER_DIR / "playlists"
CONFIG_DIR = USER_DIR / "config"
# Regenerable caches only; safe to delete wholesale.
CACHE_DIR = USER_DIR / "cache"

# --- Subdirectories ----------------------------------------------------------

ARTIST_IMAGES_DIR = IMAGES_DIR / "artist_images"
IMAGECACHE_DIR = CACHE_DIR / "imagecache"
# Per-track waveform envelopes for the Player Dock seek bar; regenerable.
WAVEFORMCACHE_DIR = CACHE_DIR / "waveforms"
# Downloaded chart CSVs are written at runtime, so they live under USER_DIR.
CHARTS_DIR = USER_DIR / "assets" / "charts"

# --- Helpers -----------------------------------------------------------------


def asset(path: str) -> str:
    """Return absolute path to an asset inside /assets."""
    return str(ASSETS_DIR / path)


def image(path: str) -> str:
    """Return absolute path to an image inside /images."""
    return str(IMAGES_DIR / path)


def log(path: str) -> str:
    """Return absolute path to a log file inside /logs."""
    return str(LOGS_DIR / path)


def playlist_path(path: str) -> str:
    """Return absolute path to a playlist file inside /playlists."""
    return str(PLAYLISTS_DIR / path)


def chart_data_path(path: str) -> str:
    """Return absolute path to a downloaded chart CSV inside /assets/charts."""
    return str(CHARTS_DIR / path)


def icon(name: str) -> QIcon:
    """Return a QIcon object for an asset inside /assets."""
    return QIcon(str(ASSETS_DIR / name))


def theme(name: str) -> str:
    """Return absolute path to a theme file inside /themes."""
    return str(THEMES_DIR / name)


def resolve_theme_assets(stylesheet: str) -> str:
    """Replace the ASSETS_DIR_PLACEHOLDER token in a QSS stylesheet with the absolute assets path."""
    # Qt resolves relative url() paths against the CWD, which breaks frozen builds and other launch dirs.
    return stylesheet.replace("ASSETS_DIR_PLACEHOLDER", ASSETS_DIR.as_posix())


def config(name: str) -> str:
    """Return absolute path to a config file inside /config."""
    return str(CONFIG_DIR / name)


def cache(name: str) -> str:
    """Return absolute path to a regenerable cache file inside /cache."""
    return str(CACHE_DIR / name)


def _migrate_legacy_cache_locations():
    """Move legacy cache files from config/ and images/ into cache/."""
    # Best-effort: the caches are regenerable, so failures are only logged.
    # Runs before the mkdir loop, so moving images/imagecache/ renames instead of nesting.
    from src.foundation.logger_config import logger

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    legacy_moves = [(CONFIG_DIR / "analysis_cache.json", CACHE_DIR / "analysis_cache.json"), (IMAGES_DIR / "imagecache", IMAGECACHE_DIR)]
    for old_path, new_path in legacy_moves:
        if not old_path.exists() or new_path.exists():
            continue
        try:
            shutil.move(str(old_path), str(new_path))
            logger.info(f"Relocated legacy cache {old_path} -> {new_path}")
        except OSError as e:
            logger.warning(f"Could not relocate legacy cache {old_path}: {e}")


def ensure_directories_exist():
    """Create any missing project directories."""
    from src.foundation.logger_config import logger

    _migrate_legacy_cache_locations()

    for path in [ASSETS_DIR, IMAGES_DIR, LOGS_DIR, PLAYLISTS_DIR, CONFIG_DIR, ARTIST_IMAGES_DIR, IMAGECACHE_DIR, WAVEFORMCACHE_DIR, THEMES_DIR, CHARTS_DIR, CACHE_DIR]:
        if not path.exists():
            logger.info(f"Creating missing directory: {path}")
        path.mkdir(parents=True, exist_ok=True)
