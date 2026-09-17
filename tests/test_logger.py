import logging
import unittest
from logging.handlers import TimedRotatingFileHandler

from core.config import ROOT_DIR, app_config
from core.logger import (
    Color,
    LOG_DIR,
    LOG_LEVEL,
    MAX_BACKUP_COUNT,
    get_logger,
)


class LoggerConfigTest(unittest.TestCase):
    def test_uses_yaml_settings_and_project_log_directory(self):
        self.assertEqual(LOG_DIR, ROOT_DIR / "log")
        self.assertEqual(
            LOG_LEVEL,
            getattr(logging, app_config["logging"]["level"].upper()),
        )
        self.assertEqual(
            MAX_BACKUP_COUNT,
            app_config["logging"]["backup_count"],
        )

    def test_initializes_console_and_rotating_file_handlers(self):
        get_logger("test_logger")
        handlers = logging.getLogger().handlers

        self.assertEqual(len(handlers), 2)
        self.assertTrue(
            any(type(item) is logging.StreamHandler for item in handlers)
        )
        self.assertTrue(
            any(isinstance(item, TimedRotatingFileHandler) for item in handlers)
        )

    def test_color_only_exposes_used_constants(self):
        color_names = {
            name
            for name in vars(Color)
            if name.isupper() and not name.startswith("__")
        }
        self.assertEqual(
            color_names,
            {
                "RESET",
                "FG_CYAN",
                "FG_BRIGHT_RED",
                "FG_BRIGHT_GREEN",
                "FG_BRIGHT_YELLOW",
                "FG_BRIGHT_WHITE",
                "BG_RED",
            },
        )


if __name__ == "__main__":
    unittest.main()
