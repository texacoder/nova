"""
Logging setup.

Technical details (including full tracebacks) go to a log file.
The terminal only ever shows friendly messages, printed by main.py.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "nova"

# Until setup_logging() runs, silently drop log records instead of letting
# Python print them to the terminal.
logging.getLogger(LOGGER_NAME).addHandler(logging.NullHandler())


def setup_logging(log_file: Path, level: str = "INFO") -> logging.Logger:
    """Send all 'nova' log messages to `log_file` (rotated at ~1 MB)."""
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False  # never print log records to the terminal

    # Remove handlers from a previous setup (useful in tests).
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    return logger


def get_logger(name: str = "") -> logging.Logger:
    """Get a logger for one part of NOVA, e.g. get_logger('memory')."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}" if name else LOGGER_NAME)
