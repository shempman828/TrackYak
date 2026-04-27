"""
runs the application
Program name: TrackYak
"""

import os
import random
import sys
import traceback

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from src.config_setup import Config
from src.db_defaults import Defaults
from src.db_tables import MusicDatabase
from src.display_settings import DisplaySettings
from src.logger_config import logger
from src.music_controller import MusicController
from src.player_mpris2 import MPRIS2Player
from src.splash_screen import StartupSplash
from src.startup_dialog import StartupDialog

try:
    from src.main_window import GUI
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
    show_status(splash, random.choice(_FUN_MESSAGES), delay=0.3)

    # Build GUI controller
    show_status(splash, "Building interface...")
    controller = MusicController()
    show_status(splash, random.choice(_FUN_MESSAGES), delay=0.3)

    # Initialize main window
    window = GUI(controller)
    show_status(splash, "Almost ready…", delay=0.4)
    logger.info("Main window initialized")
    splash.finish(window)

    # Start MPRIS2 and attach to window to prevent garbage collection
    mpris = MPRIS2Player(controller.mediaplayer)
    mpris.start()
    window._mpris = mpris

    return window, display_settings


def main() -> None:
    """Main entry point for the TrackYak application."""
    try:
        # Check Python version
        if sys.version_info < (3, 9):
            raise RuntimeError("Python 3.9 or higher required")

        # Change #1 — display backend now configured via an explicit function call
        configure_display_backend()

        # Initialize Qt application FIRST for splash screen
        app = QApplication(sys.argv)
        app.setApplicationName("TrackYak")
        app.setApplicationVersion("0.4")

        # Change #2 — single Config() instance created here and passed through
        config = Config()

        # Handle first run configuration
        if not handle_first_run(config):
            sys.exit(0)

        # Create and show splash screen
        splash = StartupSplash(min_duration_ms=500)
        splash.show()
        splash.update_status("Starting application...")

        # Pass config into initialize_application to avoid a second Config() call
        window, display_settings = initialize_application(splash, app, config)

        # Final update before showing main window
        splash.update_status("Ready!")

        window.show()

        # Store display_settings on the app instance for global access if needed
        app.display_settings = display_settings

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
            None,
            "Fatal Error",
            f"A fatal error occurred:\n{launch_error}\n\nSee log for details.",
        )
        sys.exit(1)
