# paths.py
from pathlib import Path
import shutil
import sys

from PySide6.QtGui import QIcon

# --- Base Directories --------------------------------------------------------

# Handle both development and frozen (PyInstaller / fbs) modes
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys._MEIPASS)  # temporary folder when frozen
else:
    BASE_DIR = Path(__file__).resolve().parents[2]

# --- Core Directories --------------------------------------------------------

ASSETS_DIR = BASE_DIR / "assets"
IMAGES_DIR = BASE_DIR / "images"
LOGS_DIR = BASE_DIR / "logs"
PLAYLISTS_DIR = BASE_DIR / "playlists"
THEMES_DIR = BASE_DIR / "themes"
CONFIG_DIR = BASE_DIR / "config"
# Top-level home for regenerable on-disk caches (e.g. sync's MP3 transcode
# cache). Safe to delete wholesale; never holds source-of-truth data.
CACHE_DIR = BASE_DIR / "cache"

# --- Subdirectories ----------------------------------------------------------

ARTIST_IMAGES_DIR = IMAGES_DIR / "artist_images"
PUBLISHER_LOGOS_DIR = IMAGES_DIR / "publisher_logos"
IMAGECACHE_DIR = CACHE_DIR / "imagecache"
CHARTS_DIR = ASSETS_DIR / "charts"

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
    """Return absolute path to a theme file inside /assets/themes."""
    return str(THEMES_DIR / name)


def resolve_theme_assets(stylesheet: str) -> str:
    """Substitute the ASSETS_DIR_PLACEHOLDER token in a QSS stylesheet.

    Qt resolves relative url() paths in a stylesheet against the process's
    working directory, which breaks in packaged/frozen builds or when the
    app is launched from elsewhere. ASSETS_DIR already accounts for that.
    """
    return stylesheet.replace("ASSETS_DIR_PLACEHOLDER", ASSETS_DIR.as_posix())


def config(name: str) -> str:
    """Return absolute path to a config file inside /config."""
    return str(CONFIG_DIR / name)


def cache(name: str) -> str:
    """Return absolute path to a regenerable cache file inside /cache."""
    return str(CACHE_DIR / name)


def _migrate_legacy_cache_locations():
    """One-time relocate of regenerable caches that predate CACHE_DIR.

    ``analysis_cache.json`` used to live in ``config/`` and the artwork
    thumbnail cache in ``images/imagecache/``; both now belong under
    ``cache/``. Every entry here is fully regenerable, so any failure is
    logged and ignored — the cache just rebuilds in its new home. Runs
    before the mkdir loop so ``shutil.move`` of the old ``imagecache/``
    directory renames cleanly instead of nesting inside a fresh target.
    """
    from src.foundation.logger_config import logger

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    legacy_moves = [
        (CONFIG_DIR / "analysis_cache.json", CACHE_DIR / "analysis_cache.json"),
        (IMAGES_DIR / "imagecache", IMAGECACHE_DIR),
    ]
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

    for path in [
        ASSETS_DIR,
        IMAGES_DIR,
        LOGS_DIR,
        PLAYLISTS_DIR,
        ARTIST_IMAGES_DIR,
        PUBLISHER_LOGOS_DIR,
        IMAGECACHE_DIR,
        THEMES_DIR,
        CHARTS_DIR,
        CACHE_DIR,
    ]:
        if not path.exists():
            logger.info(f"Creating missing directory: {path}")
        path.mkdir(parents=True, exist_ok=True)
