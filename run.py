"""
runs the application
Program name: TrackYak
"""

import gc
import os
import random
import sys
import traceback

from src.core.installation_check import verify_installation

# Runs before any third-party import (PySide6 included) so a broken
# install prints a clear, actionable message instead of a raw traceback.
verify_installation()

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from src.core.asset_paths import ensure_directories_exist
from src.core.config_setup import Config
from src.core.logger_config import logger
from src.core.splash_screen import StartupSplash
from src.core.startup_dialog import StartupDialog
from src.db.db_defaults import Defaults
from src.db.db_tables import MusicDatabase
from src.display.display_settings import DisplaySettings
from src.image.artwork_cache import ArtworkCache
from src.musicbrainz.musicbrainz_core import configure as configure_musicbrainz
from src.player.music_controller import MusicController
from src.player.player_mpris2 import MPRIS2Player

try:
    from src.core.main_window import GUI
except ImportError as ie:
    logger.error(f"Missing required module: {ie}")
    sys.exit(1)


_FUN_MESSAGES = [
    "Reticulating musical splines… 🎵",
    "Feeding the audio gremlins… 👹",
    "Calculating the bison-to-yak ratio… 🐂",
    "Tuning frequencies beyond human hearing… 🔊",
    "Counting the beats per minute… ⏱️",
    "Teaching yaks to whistle… 🐂🎶",
    "Herding rogue sound waves… 🌊",
    "Calculating probability of funk… 🎷",
    "Turning up the volume to 11… 🎛️🎶",
    "Warming the tubes in the preamp… 🔥🎛️",
]


def configure_display_backend() -> None:
    """Detect the active display session and set the appropriate Qt/GDK backends."""
    session = os.environ.get("XDG_SESSION_TYPE", "").lower()

    if session == "wayland":
        os.environ["QT_QPA_PLATFORM"] = "wayland"
        os.environ["QT_QPA_PLATFORMTHEME"] = "xdgdesktopportal"
    else:
        os.environ["QT_QPA_PLATFORM"] = "xcb"
        os.environ["GDK_BACKEND"] = "x11"


def _prewarm_webengine(window) -> None:
    """Force the main window's native (X11) window handle to be created
    before it's ever shown, by giving it a throwaway QWebEngineView child.

    QtWebEngine's GPU-compositing surface needs a native-backed widget
    hierarchy. The first time a QWebEngineView is added anywhere under an
    already-visible top-level window, Qt has to recreate that window's
    native handle to back it -- on X11 this is visible as a brief
    unmap/remap (the window appears to minimize and resize). Paying that
    one-time cost here, while the window is still hidden, keeps it
    invisible to the user. Confirmed empirically: a standalone prewarmed
    QWebEngineView (not parented into the window) or a bare winId() call
    does NOT prevent the glitch -- it has to be a native-child widget
    inside this window's own hierarchy.
    """
    from PySide6.QtWebEngineWidgets import QWebEngineView

    probe = QWebEngineView(window)
    probe.setHtml("<html></html>")
    probe.resize(1, 1)
    probe.show()
    QApplication.processEvents()
    probe.hide()
    probe.deleteLater()


def show_status(splash, message: str, delay: float = 0) -> None:
    """Update the splash screen status text, then optionally pause briefly.

    Args:
        splash:  The StartupSplash instance to update.
        message: Status text to display.a
        delay:   Seconds to pause after updating (0 = no pause).
    """
    splash.update_status(message)
    QApplication.processEvents()
    if delay > 0:
        loop = QEventLoop()
        QTimer.singleShot(int(delay * 1000), loop.quit)
        loop.exec()


def handle_first_run(config: Config) -> bool:
    """Handle first-run configuration if needed."""
    if config.is_first_run():
        logger.info("First run detected - showing configuration dialog")

        first_run_dialog = StartupDialog(config)
        first_run_dialog.setWindowTitle("First Run Setup - Music Library")

        # Modal execution - user must complete setup
        if first_run_dialog.exec() != QDialog.Accepted:
            logger.info("First run setup cancelled by user")
            return False

        logger.info("First run configuration completed successfully")
    return True


def initialize_application(splash, app, config: Config):
    """Initialize application components with progress updates and fun messages.

    The MPRIS2Player instance is intentionally attached to the window object
    (window._mpris) to keep it alive for the lifetime of the application.
    Without this, Python's garbage collector would destroy it shortly after
    this function returns.

    Args:
        splash: The StartupSplash instance for status updates.
        app:    The QApplication instance.
        config: The already-created Config instance from main().

    Returns:
        A tuple of (window, display_settings).
    """
    # Show a random fun message first
    show_status(splash, random.choice(_FUN_MESSAGES), delay=0.5)

    # MusicBrainz client needs a User-Agent set before any lookup call
    configure_musicbrainz()

    # Database initialization
    show_status(splash, "Initializing database...")
    db = MusicDatabase()  # Single instantiation
    logger.info("Database initialized successfully")
    show_status(splash, random.choice(_FUN_MESSAGES), delay=0.3)

    # Loading defaults
    show_status(splash, "Loading library data...")
    defaults = Defaults(db.Session)
    defaults.insert_defaults()
    logger.info("Default entities inserted successfully")
    show_status(splash, random.choice(_FUN_MESSAGES), delay=0.6)

    # Initialize DisplaySettings using the config passed in from main()
    show_status(splash, "Loading configuration...")
    show_status(splash, random.choice(_FUN_MESSAGES), delay=0.2)

    # Configure display
    show_status(splash, "Configuring display...")
    display_settings = DisplaySettings(app, config)
    display_settings.apply_all()
    # Attach to the QApplication instance before the window is built so
    # GUI._init_menu_bar() can resolve it (it reads app.display_settings
    # to apply the saved menu-bar auto-hide preference on startup).
    app.display_settings = display_settings
    show_status(splash, random.choice(_FUN_MESSAGES), delay=0.3)

    # Album art thumbnail cache - attach before the window is built so
    # widgets can resolve it (get_artwork_cache()) while rendering album art.
    app.artwork_cache = ArtworkCache()

    # Build GUI controller
    show_status(splash, "Building interface...")
    controller = MusicController()
    show_status(splash, random.choice(_FUN_MESSAGES), delay=0.3)

    # Initialize main window
    window = GUI(controller)

    # Absorb the first-QWebEngineView native-window-recreation glitch (see
    # _prewarm_webengine docstring) while the window is still hidden, before
    # Places/Influences can trigger it visibly on first open.
    _prewarm_webengine(window)

    show_status(splash, "Almost ready…", delay=0.4)
    logger.info("Main window initialized")

    show_status(splash, "Ready!", delay=0.2)
    splash.finish()

    # Start MPRIS2 and attach to window to prevent garbage collection
    mpris = MPRIS2Player(controller.mediaplayer)
    mpris.start()
    window._mpris = mpris

    # Everything above is permanent for the process lifetime (Qt widget tree,
    # ORM classes, loaded modules). Freeze it out of GC bookkeeping so no future
    # cyclic collection walks it again -- that keeps the collections that still
    # run, including the one MusicPlayer forces when it closes a stream, short.
    # Directly relevant to playback hitching: a long GC pause freezes the audio
    # callback thread too.
    gc.collect()
    gc.freeze()

    return window, display_settings


def main() -> None:
    """Main entry point for the TrackYak application."""
    try:
        configure_display_backend()

        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)

        # Initialize Qt application FIRST for splash screen
        app = QApplication(sys.argv)
        app.setApplicationName("TrackYak")
        app.setApplicationVersion("0.5")

        config = Config()

        # Developer mode: self-contained opt-in package that patches itself
        # into the app. Patches are installed unconditionally; the [developer]
        # config flag gates what they actually expose.
        try:
            from src.dev import install as install_dev_mode

            install_dev_mode()
        except Exception:
            logger.exception("developer-mode install failed; continuing without it")

        # Create any missing asset/data directories (e.g. assets/charts)
        # before anything tries to read or write into them.
        ensure_directories_exist()

        # Handle first run configuration
        if not handle_first_run(config):
            sys.exit(0)

        # Create and show splash screen
        splash = StartupSplash(min_duration_ms=500)
        splash.show()
        splash.update_status("Starting application...")

        try:
            # Pass config into initialize_application to avoid a second Config() call
            window, _display_settings = initialize_application(splash, app, config)
        except Exception:
            splash.close()
            raise

        window.show()

        # Start application loop
        sys.exit(app.exec())

    except Exception as e:
        logger.error(f"Unhandled exception in main: {e}")
        raise


if __name__ == "__main__":
    try:
        logger.info("Application starting")
        main()
    except Exception as launch_error:
        logger.error(f"Fatal error during application launch: {launch_error}")
        traceback_str = "".join(traceback.format_tb(launch_error.__traceback__))
        logger.error(f"Traceback:\n{traceback_str}")
        QMessageBox.critical(
            None, "Fatal Error", f"A fatal error occurred:\n{launch_error}\n\nSee log for details."
        )
        sys.exit(1)
