"""App-wide logger setup with console and rotating-file handlers."""

import logging
from logging.handlers import RotatingFileHandler

from src.foundation.asset_paths import LOGS_DIR


def setup_logging(config=None):
    """Configure and return the app logger, using `config` values when given."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = str(LOGS_DIR / "app.log")

    logger = logging.getLogger("musiclib")
    logger.setLevel(logging.DEBUG)  # Lowest level; the handlers filter.
    # Do not also pass records to root handlers, which would print each message two times.
    logger.propagate = False

    # Close removed handlers, or each reconfigure leaks an open log file handle.
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()

    # Create formatter
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s() - %(message)s")

    # Setup handlers based on config
    if config:
        log_level = config.get_logging_level()
        console_enabled = config.is_console_logging_enabled()
        file_enabled = config.is_file_logging_enabled()
        max_file_size = config.get_max_file_size_mb() * 1024 * 1024  # Convert to bytes
        backup_count = config.get_backup_count()
    else:
        # Default values if no config provided
        log_level = logging.DEBUG
        console_enabled = True
        file_enabled = True
        max_file_size = 10 * 1024 * 1024  # 10 MB
        backup_count = 14

    # Console handler
    if console_enabled:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # File handler with rotation
    if file_enabled:
        file_handler = RotatingFileHandler(log_file, maxBytes=max_file_size, backupCount=backup_count, encoding="utf-8")
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


# Create logger instance (will be reconfigured later if config is available)
logger = setup_logging()


def reconfigure_logging(config):
    """Rebuild the app logger's handlers from `config`."""
    global logger
    logger = setup_logging(config)
