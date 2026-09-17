"""Central logging setup for Camera Server."""

import logging
import threading
from logging.handlers import TimedRotatingFileHandler

from core.config import ROOT_DIR, app_config


_INITIALIZED = False
_INIT_LOCK = threading.Lock()
LOG_FORMAT = "[%(asctime)s.%(msecs)03d] [%(levelname)s] [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class Color:
    RESET = "\033[0m"
    CYAN = "\033[36m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_RED = "\033[91m"
    CRITICAL = "\033[41m\033[97m"


class ColoredFormatter(logging.Formatter):
    LEVEL_COLORS = {
        logging.DEBUG: Color.CYAN,
        logging.INFO: Color.BRIGHT_GREEN,
        logging.WARNING: Color.BRIGHT_YELLOW,
        logging.ERROR: Color.BRIGHT_RED,
        logging.CRITICAL: Color.CRITICAL,
    }

    def format(self, record):
        message = super().format(record)
        color = self.LEVEL_COLORS.get(record.levelno, Color.RESET)
        return f"{color}{message}{Color.RESET}"


def init_logging() -> logging.Logger:
    """Initialize console and daily rotating-file logging once."""
    global _INITIALIZED

    if _INITIALIZED:
        return logging.getLogger()

    with _INIT_LOCK:
        if _INITIALIZED:
            return logging.getLogger()

        log_dir = ROOT_DIR / app_config.logging.directory
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / app_config.logging.filename

        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        root_logger.setLevel(getattr(logging, app_config.logging.level))

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(ColoredFormatter(LOG_FORMAT, DATE_FORMAT))
        root_logger.addHandler(console_handler)

        file_handler = TimedRotatingFileHandler(
            filename=log_path,
            when="midnight",
            interval=1,
            backupCount=app_config.logging.backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        root_logger.addHandler(file_handler)

        _INITIALIZED = True
        return root_logger


def get_logger(name: str) -> logging.Logger:
    if not _INITIALIZED:
        init_logging()
    return logging.getLogger(name)
