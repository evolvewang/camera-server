import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from core.config import app_config

LOG_DIR = Path(app_config['project']['directory']) / "log"
LOG_FILENAME = app_config["project"]["name"] + ".log"
LOG_LEVEL = getattr(logging, app_config["logging"]["level"].upper())

BACKUP_COUNT = app_config["logging"]["backup_count"]  # 备份文件数量上限
LOG_FORMAT = "[%(asctime)s.%(msecs)03d] [%(levelname)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_INITIALIZED = False  # 模块级变量 初始化标识


class Color:
    """日志实际使用的 ANSI 颜色与样式常量"""
    RESET = '\033[0m'  # 重置

    FG_CYAN = '\033[36m'
    FG_BRIGHT_RED = '\033[91m'
    FG_BRIGHT_GREEN = '\033[92m'
    FG_BRIGHT_YELLOW = '\033[93m'
    FG_BRIGHT_WHITE = '\033[97m'

    BG_RED = '\033[41m'


class ColoredFormatter(logging.Formatter):
    LEVEL_COLORS = {
        logging.DEBUG: Color.FG_CYAN,
        logging.INFO: Color.FG_BRIGHT_GREEN,
        logging.WARNING: Color.FG_BRIGHT_YELLOW,
        logging.ERROR: Color.FG_BRIGHT_RED,
        logging.CRITICAL: Color.BG_RED + Color.FG_BRIGHT_WHITE,
    }

    def format(self, record):
        # 获取原始消息
        log_message = super().format(record)
        # 根据级别获取颜色
        color = self.LEVEL_COLORS.get(record.levelno, Color.RESET)
        # 返回带颜色的消息
        return f"{color}{log_message}{Color.RESET}"


def init_logging() -> logging.Logger:
    """初始化logging配置"""

    global _INITIALIZED  # 声明为模块级变量

    if _INITIALIZED:
        return logging.getLogger()

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger()  # 全局logger对象
    logger.handlers.clear()

    logger.setLevel(LOG_LEVEL)

    color_formatter = ColoredFormatter(LOG_FORMAT, datefmt=DATE_FORMAT)  # 控制台彩色
    base_formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # console
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(color_formatter)
    logger.addHandler(stream_handler)

    # file
    file_handler = TimedRotatingFileHandler(  # TODO 考虑多进程安全问题
        filename=LOG_DIR / LOG_FILENAME,
        when="midnight",
        interval=1,
        backupCount=BACKUP_COUNT,
        encoding="utf-8"
    )
    file_handler.setFormatter(base_formatter)
    logger.addHandler(file_handler)

    _INITIALIZED = True  # 修改初始化状态

    return logger


def get_logger(name):
    if not _INITIALIZED:
        init_logging()
    return logging.getLogger(name)


if __name__ == '__main__':
    logger = get_logger(__name__)
    logger.debug('debug')
    logger.info('info')
    logger.warning('warning')
    logger.error('error')