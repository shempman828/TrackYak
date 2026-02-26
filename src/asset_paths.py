# paths.py
from pathlib import Path
import sys
from PySide6.QtGui import QIcon

# --- Base Directories --------------------------------------------------------

# Handle both development and frozen (PyInstaller / fbs) modes
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys._MEIPASS)  # temporary folder when frozen
else:
    BASE_DIR = Path(__file__).resolve().parents[1]

# --- Core Directories --------------------------------------------------------

ASSETS_DIR = BASE_DIR / "assets"
IMAGES_DIR = BASE_DIR / "images"
LOGS_DIR = BASE_DIR / "logs"
PLAYLISTS_DIR = BASE_DIR / "playlists"
THEMES_DIR = BASE_DIR / "themes"
CONFIG_DIR = BASE_DIR / "config"

# --- Subdirectories ----------------------------------------------------------

ALBUM_ART_DIR = IMAGES_DIR / "album_art"
ARTIST_IMAGES_DIR = IMAGES_DIR / "artist_images"
PUBLISHER_LOGOS_DIR = IMAGES_DIR / "publisher_logos"
IMAGECACHE_DIR = IMAGES_DIR / "imagecache"

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


def icon(name: str) -> QIcon:
    """Return a QIcon object for an asset inside /assets."""
    return QIcon(str(ASSETS_DIR / name))


def theme(name: str) -> str:
    """Return absolute path to a theme file inside /assets/themes."""
    return str(THEMES_DIR / name)


def config(name: str) -> str:
    """Return absolute path to a config file inside /config."""
    return str(CONFIG_DIR / name)


def ensure_directories_exist():
    """Create any missing project directories."""
    for path in [
        ASSETS_DIR,
        IMAGES_DIR,
        LOGS_DIR,
        PLAYLISTS_DIR,
        ALBUM_ART_DIR,
        ARTIST_IMAGES_DIR,
        PUBLISHER_LOGOS_DIR,
        IMAGECACHE_DIR,
        THEMES_DIR,
    ]:
        path.mkdir(parents=True, exist_ok=True)
