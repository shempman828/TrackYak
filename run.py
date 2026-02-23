"""
runs the application
Program name: TrackYak
"""

import os

os.environ["QT_QPA_PLATFORM"] = "xcb"
import random
import sys
import traceback

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from config_setup import Config
from db_defaults import Defaults
from db_tables import MusicDatabase
from display_settings import DisplaySettings
from logger_config import logger
from music_controller import MusicController
from splash_screen import StartupSplash
from startup_dialog import StartupDialog

try:
    from main_window import GUI
except ImportError as ie:
    logger.error(f"Missing required module: {ie}")
    sys.exit(1)


def handle_first_run(config):
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


def initialize_application(splash, app):
    """Initialize application components with progress updates and fun messages."""

    fun_messages = [
        "Reticulating musical splines… 🎵",
        "Feeding the audio gremlins… 👹",
        "Calculating the sound-to-yak ratio… 🐂",
        "Tuning frequencies beyond human hearing… 🔊",
        "Polishing the vinyl dust… 💿",
        "Counting the beats per minute… ⏱️",
        "Sampling the samples… 🎤",
        "Transposing invisible sheet music… 🎼",
        "Teaching yaks to whistle… 🐂🎶",
        "Herding rogue sound waves… 🌊",
        "Calculating probability of funk… 🎷",
        "Feeding the algorithm its daily snack… 🍪",
        "Turning up the bass to 11… 🎛️🎶",
        "Warming the tubes in the preamp… 🔥🎛️",
    ]

    # Helper to show status with optional delay
    def show_status(message, delay=0):
        splash.update_status(message)
        QApplication.processEvents()
        if delay > 0:
            loop = QEventLoop()
            QTimer.singleShot(int(delay * 1000), loop.quit)
            loop.exec()

    # Show a random fun message first
    show_status(random.choice(fun_messages), delay=0.5)

    # Database initialization
    show_status("Initializing database...")
    db = MusicDatabase()  # Single instantiation
    logger.info("Database initialized successfully")
    show_status(random.choice(fun_messages), delay=0.3)

    # Loading defaults
    show_status("Loading library data...")
    defaults = Defaults(db.Session)
    defaults.insert_defaults()
    logger.info("Default entities inserted successfully")
    show_status(random.choice(fun_messages), delay=0.6)

    # Load config
    show_status("Loading configuration...")
    config = Config()
    show_status(random.choice(fun_messages), delay=0.2)

    # Initialize DisplaySettings with app instance
    show_status("Configuring display...")
    display_settings = DisplaySettings(app, config)
    display_settings.apply_all()
    show_status(random.choice(fun_messages), delay=0.3)

    # Build GUI controller
    show_status("Building interface...")
    controller = MusicController()
    show_status(random.choice(fun_messages), delay=0.3)

    # Initialize main window with display_settings
    window = GUI(controller)
    show_status("Almost ready…", delay=0.4)
    logger.info("Main window initialized")
    splash.finish(window)

    return window, display_settings


def main() -> None:
    """
    Main entry point for the Music Library application.
    """
    try:
        # Check Python version
        if sys.version_info < (3, 9):
            raise RuntimeError("Python 3.9 or higher required")

        # Initialize Qt application FIRST for splash screen
        app = QApplication(sys.argv)
        app.setApplicationName("TrackYak")
        app.setApplicationVersion("0.4")

        # Create config instance to check for first run
        config = Config()

        # Handle first run configuration
        if not handle_first_run(config):
            sys.exit(0)

        # Create and show splash screen
        splash = StartupSplash(min_duration_ms=500)
        splash.show()
        splash.update_status("Starting application...")

        # Initialize application components with app instance
        window, display_settings = initialize_application(splash, app)  # Pass app here

        # Final update before showing main window
        splash.update_status("Ready!")

        # Set main window to be initially transparent for fade-in
        window.show()

        # Store display_settings reference if needed globally
        app.display_settings = (
            display_settings  # Optional: store on app for global access
        )

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
