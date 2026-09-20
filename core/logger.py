import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional

from core.config import ROOT_DIR, app_config

LOG_DIR = ROOT_DIR / "log"
LOG_FILENAME = app_config["logging"].get(
    "filename", f'{app_config["project"]["name"]}.log'
)
LOG_LEVEL = getattr(logging, app_config["logging"]["level"].upper())

BACKUP_COUNT = app_config["logging"]["backup_count"]  # 备份文件数量上限
LOG_FORMAT = "[%(asctime)s.%(msecs)03d] [%(levelname)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_INITIALIZED = False


class Color:
    """ANSI colors used by the console formatter."""

    RESET = "\033[0m"

    FG_CYAN = "\033[36m"
    FG_BRIGHT_RED = "\033[91m"
    FG_BRIGHT_GREEN = "\033[92m"
    FG_BRIGHT_YELLOW = "\033[93m"
    FG_BRIGHT_WHITE = "\033[97m"

    BG_RED = "\033[41m"


class ColoredFormatter(logging.Formatter):
    LEVEL_COLORS = {
        logging.DEBUG: Color.FG_CYAN,
        logging.INFO: Color.FG_BRIGHT_GREEN,
        logging.WARNING: Color.FG_BRIGHT_YELLOW,
        logging.ERROR: Color.FG_BRIGHT_RED,
        logging.CRITICAL: Color.BG_RED + Color.FG_BRIGHT_WHITE,
    }

    def format(self, record: logging.LogRecord) -> str:
        log_message = super().format(record)
        color = self.LEVEL_COLORS.get(record.levelno, Color.RESET)
        return f"{color}{log_message}{Color.RESET}"


def init_logging() -> logging.Logger:
    """Initialize the process-wide console and rotating file handlers."""

    global _INITIALIZED

    if _INITIALIZED:
        return logging.getLogger()

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger()
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()

    logger.setLevel(LOG_LEVEL)

    color_formatter = ColoredFormatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    base_formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(color_formatter)
    logger.addHandler(stream_handler)

    file_handler = TimedRotatingFileHandler(
        filename=LOG_DIR / LOG_FILENAME,
        when="midnight",
        interval=1,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(base_formatter)
    logger.addHandler(file_handler)

    _INITIALIZED = True  # 修改初始化状态

    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    if not _INITIALIZED:
        init_logging()
    return logging.getLogger(name)


if __name__ == "__main__":
    logger = get_logger(__name__)
    logger.debug('debug')
    logger.info('info')
    logger.warning('warning')
    logger.error('error')
