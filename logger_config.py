import logging
from logging.handlers import RotatingFileHandler
import os


def setup_logging(config=None):
    """Setup logging with optional configuration"""
    LOG_DIR = "logs"
    os.makedirs(LOG_DIR, exist_ok=True)
    LOG_FILE = os.path.join(LOG_DIR, "app.log")

    # Get logger
    logger = logging.getLogger("musiclib")
    logger.setLevel(logging.DEBUG)  # Set to lowest level, handlers will filter

    # Clear any existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Create formatter
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s() - %(message)s"
    )

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
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=max_file_size, backupCount=backup_count, encoding="utf-8"
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


# Create logger instance (will be reconfigured later if config is available)
logger = setup_logging()


def reconfigure_logging(config):
    """Reconfigure logging with new settings"""
    global logger
    logger = setup_logging(config)
